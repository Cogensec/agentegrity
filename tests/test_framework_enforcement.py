"""Under enforce=True a blocked tool call never runs, in every framework that can stop one.

Each test drives the real framework with a scripted offline model and a tool
that records whether its body executed. The verdict comes from a one-layer
evaluator, so the tests exercise the adapters' wiring, not the detectors.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from agentegrity import IntegrityViolationError
from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.core.evaluator import IntegrityEvaluator, LayerResult
from agentegrity.core.profile import AgentProfile

from .test_usage_framework_adapters import _needs


class _Verdict:
    """A layer that returns one fixed action."""

    name = "verdict"

    def __init__(self, action: str) -> None:
        self._action = action

    def evaluate(self, profile: Any, context: dict[str, Any] | None = None) -> LayerResult:
        passed = self._action == "pass"
        return LayerResult(layer_name=self.name, score=1.0 if passed else 0.0,
                           passed=passed, action=self._action)


class _Crash:
    """A layer whose evaluation raises."""

    name = "crash"

    def evaluate(self, profile: Any, context: dict[str, Any] | None = None) -> LayerResult:
        raise RuntimeError("detector failed")


def _evaluator(action: str) -> IntegrityEvaluator:
    return IntegrityEvaluator(layers=[_Crash() if action == "crash" else _Verdict(action)])


def _kwargs(action: str, enforce: bool) -> dict[str, Any]:
    return {"profile": AgentProfile.default(name="t"), "evaluator": _evaluator(action),
            "enforce": enforce}


def _denied(decision: dict[str, Any]) -> bool:
    return decision.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


async def _pre_tool(adapter: ClaudeAdapter) -> dict[str, Any]:
    return await adapter.on_event("pre_tool_use", {"tool_name": "Bash",
                                                   "tool_input": {"command": "ls"}})


class TestEvaluationFailure:
    @pytest.mark.asyncio
    async def test_crash_denies_under_enforcement(self) -> None:
        decision = await _pre_tool(ClaudeAdapter(**_kwargs("crash", enforce=True)))
        assert _denied(decision)
        assert "RuntimeError" in decision["hookSpecificOutput"]["permissionDecisionReason"]

    @pytest.mark.asyncio
    async def test_crash_allows_without_enforcement(self) -> None:
        assert await _pre_tool(ClaudeAdapter(**_kwargs("crash", enforce=False))) == {}

    @pytest.mark.asyncio
    async def test_crash_outside_a_tool_call_never_denies(self) -> None:
        adapter = ClaudeAdapter(**_kwargs("crash", enforce=True))
        assert await adapter.on_event("user_prompt_submit", {"prompt": "hi"}) == {}


@_needs("langchain_core")
class TestLangChain:
    @staticmethod
    def _tool(ran: list[str]) -> Any:
        from langchain_core.tools import tool

        @tool
        def run_shell(command: str) -> str:
            """Run a shell command."""
            ran.append(command)
            return "ok"

        return run_shell

    @staticmethod
    def _instrumented(action: str, enforce: bool, ran: list[str]) -> Any:
        from langchain_core.runnables import RunnableLambda

        from agentegrity.adapters.langchain import LangChainAdapter

        adapter = LangChainAdapter(**_kwargs(action, enforce))
        chain = RunnableLambda(lambda x: x) | TestLangChain._tool(ran)
        return adapter.instrument_chain(chain)

    def test_block_stops_the_tool(self) -> None:
        ran: list[str] = []
        with pytest.raises(IntegrityViolationError, match="block"):
            self._instrumented("block", True, ran).invoke({"command": "ls"})
        assert ran == []

    def test_block_stops_the_tool_on_the_async_path(self) -> None:
        ran: list[str] = []
        with pytest.raises(IntegrityViolationError):
            asyncio.run(self._instrumented("block", True, ran).ainvoke({"command": "ls"}))
        assert ran == []

    def test_escalate_without_approval_stops_the_tool(self) -> None:
        ran: list[str] = []
        with pytest.raises(IntegrityViolationError, match="escalate"):
            self._instrumented("escalate", True, ran).invoke({"command": "ls"})
        assert ran == []

    def test_pass_runs_the_tool(self) -> None:
        ran: list[str] = []
        assert self._instrumented("pass", True, ran).invoke({"command": "ls"}) == "ok"
        assert ran == ["ls"]

    def test_block_without_enforcement_only_records(self) -> None:
        ran: list[str] = []
        assert self._instrumented("block", False, ran).invoke({"command": "ls"}) == "ok"
        assert ran == ["ls"]


@_needs("agents")
class TestOpenAIAgents:
    @staticmethod
    def _run(action: str, enforce: bool, ran: list[str]) -> tuple[Any, Any]:
        from agents import Agent, Runner, function_tool
        from agents.items import ModelResponse
        from agents.models.interface import Model
        from agents.usage import Usage
        from openai.types.responses import (
            ResponseFunctionToolCall,
            ResponseOutputMessage,
            ResponseOutputText,
        )

        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        @function_tool
        def run_shell(command: str) -> str:
            """Run a shell command."""
            ran.append(command)
            return "ok"

        class _Scripted(Model):
            def __init__(self, outputs: list[Any]) -> None:
                self._outputs = outputs

            async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
                return ModelResponse(output=[self._outputs.pop(0)], usage=Usage(),
                                     response_id=None)

            def stream_response(self, *args: Any, **kwargs: Any) -> Any:
                raise NotImplementedError

        call = ResponseFunctionToolCall(type="function_call", call_id="c1", name="run_shell",
                                        arguments=json.dumps({"command": "ls"}))
        text = ResponseOutputMessage(
            id="m1", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
        )
        adapter = OpenAIAgentsAdapter(**_kwargs(action, enforce))
        agent = Agent(name="a", model=_Scripted([call, text]), tools=[run_shell])
        result = asyncio.run(Runner.run(agent, "hi", hooks=adapter.create_run_hooks()))
        return adapter, result

    def test_block_stops_the_tool(self) -> None:
        ran: list[str] = []
        with pytest.raises(IntegrityViolationError, match="block"):
            self._run("block", True, ran)
        assert ran == []

    def test_pass_runs_the_tool(self) -> None:
        ran: list[str] = []
        _, result = self._run("pass", True, ran)
        assert ran == ["ls"]
        assert result.final_output == "done"

    def test_the_call_is_evaluated_with_its_arguments(self) -> None:
        adapter, _ = self._run("pass", False, [])
        event = next(e for e in adapter.events if e.event_type == "pre_tool_use")
        assert event.data["tool_input"] == {"command": "ls"}


@_needs("crewai")
class TestCrewAI:
    @pytest.fixture(autouse=True)
    def _offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED"):
            monkeypatch.setenv(name, "true")
        monkeypatch.setenv("CREWAI_TRACING_ENABLED", "false")

    @staticmethod
    def _crew(ran: list[str]) -> Any:
        from crewai import Agent, Crew, Task
        from crewai.llms.base_llm import BaseLLM
        from crewai.tools import tool

        @tool("run_shell")
        def run_shell(command: str) -> str:
            """Run a shell command."""
            ran.append(command)
            return "ok"

        class _Scripted(BaseLLM):
            replies: list[str] = []

            def call(self, *args: Any, **kwargs: Any) -> str:
                return self.replies.pop(0)

            def supports_function_calling(self) -> bool:
                return False

        llm = _Scripted(model="scripted", replies=[
            'Thought: run it\nAction: run_shell\nAction Input: {"command": "ls"}',
            "Thought: done\nFinal Answer: finished",
        ])
        agent = Agent(role="r", goal="g", backstory="b", llm=llm, tools=[run_shell])
        return Crew(agents=[agent], tasks=[Task(description="d", expected_output="e",
                                                agent=agent)])

    def _kickoff(self, action: str, enforce: bool, ran: list[str],
                 scoped: bool = True) -> Any:
        from agentegrity.adapters.crewai import CrewAIAdapter

        crew = self._crew(ran)
        adapter = CrewAIAdapter(**_kwargs(action, enforce))
        adapter.subscribe(crew if scoped else None)
        try:
            crew.kickoff()
        finally:
            adapter.close()
        return adapter

    def test_block_stops_the_tool(self) -> None:
        ran: list[str] = []
        self._kickoff("block", True, ran)
        assert ran == []

    def test_pass_runs_the_tool_and_evaluates_it_once(self) -> None:
        ran: list[str] = []
        adapter = self._kickoff("pass", True, ran)
        assert ran == ["ls"]
        calls = [e for e in adapter.events if e.event_type == "pre_tool_use"]
        assert [e.data["tool_input"] for e in calls] == [{"command": "ls"}]

    def test_a_crew_scoped_adapter_ignores_other_crews(self) -> None:
        from agentegrity.adapters.crewai import CrewAIAdapter

        ran: list[str] = []
        adapter = CrewAIAdapter(**_kwargs("block", True))
        adapter.subscribe(self._crew([]))
        try:
            self._crew(ran).kickoff()
        finally:
            adapter.close()
        assert ran == ["ls"]

    def test_close_removes_the_hook(self) -> None:
        ran: list[str] = []
        self._kickoff("block", True, [], scoped=False)
        self._crew(ran).kickoff()
        assert ran == ["ls"]


@_needs("google.adk")
class TestGoogleADK:
    @staticmethod
    def _run(action: str, enforce: bool, ran: list[str]) -> list[dict[str, Any]]:
        from google.adk.agents import LlmAgent
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_request import LlmRequest
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from agentegrity.adapters.google_adk import GoogleADKAdapter

        def run_shell(command: str) -> str:
            """Run a shell command."""
            ran.append(command)
            return "ok"

        tool_results: list[dict[str, Any]] = []

        class _Scripted(BaseLlm):
            replies: list[LlmResponse] = []

            async def generate_content_async(
                self, llm_request: LlmRequest, stream: bool = False,
            ) -> AsyncGenerator[LlmResponse, None]:
                for content in llm_request.contents:
                    for part in content.parts or []:
                        if part.function_response is not None:
                            tool_results.append(dict(part.function_response.response or {}))
                yield self.replies.pop(0)

        call = types.Part(function_call=types.FunctionCall(name="run_shell",
                                                           args={"command": "ls"}))
        model = _Scripted(model="scripted", replies=[
            LlmResponse(content=types.Content(role="model", parts=[call])),
            LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")])),
        ])
        agent = LlmAgent(name="a", model=model, tools=[run_shell])
        GoogleADKAdapter(**_kwargs(action, enforce)).instrument(agent)

        async def main() -> None:
            runner = InMemoryRunner(agent=agent, app_name="app")
            session = await runner.session_service.create_session(app_name="app", user_id="u")
            message = types.Content(role="user", parts=[types.Part(text="hi")])
            async for _ in runner.run_async(user_id="u", session_id=session.id,
                                            new_message=message):
                pass

        asyncio.run(main())
        return tool_results

    def test_block_stops_the_tool_and_tells_the_model(self) -> None:
        ran: list[str] = []
        results = self._run("block", True, ran)
        assert ran == []
        assert "block" in results[0]["error"]

    def test_pass_runs_the_tool(self) -> None:
        ran: list[str] = []
        self._run("pass", True, ran)
        assert ran == ["ls"]

    def test_block_without_enforcement_only_records(self) -> None:
        ran: list[str] = []
        self._run("block", False, ran)
        assert ran == ["ls"]
