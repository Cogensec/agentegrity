"""A denied tool call returns a denial to the model and the run continues (Phase 1a).

``on_deny="continue"`` (the default) hands the model a fixed denial in place of
the tool's result; ``on_deny="raise"`` ends the run with
:class:`IntegrityViolationError` where the framework can stop on an exception.
The denial names the verdict and a stable code, never the detector, the
pattern or the score, so probing denials does not teach an attacker the rule.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from agentegrity import IntegrityViolationError
from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.adapters.langchain import LangChainAdapter
from agentegrity.core.evaluator import IntegrityEvaluator, LayerResult
from agentegrity.core.profile import AgentProfile

from .test_framework_enforcement import _denied, _kwargs
from .test_usage_framework_adapters import _needs


class _Blocks:
    """Blocks calls to one tool and passes everything else."""

    name = "blocks"

    def __init__(self, tool: str) -> None:
        self._tool = tool

    def evaluate(self, profile: Any, context: dict[str, Any] | None = None) -> LayerResult:
        action = (context or {}).get("action") or {}
        blocked = action.get("tool") == self._tool
        return LayerResult(layer_name=self.name, score=0.0 if blocked else 1.0,
                           passed=not blocked, action="block" if blocked else "pass")


def _reason(decision: dict[str, Any]) -> str:
    return str(decision["hookSpecificOutput"]["permissionDecisionReason"])


async def _pre_tool(adapter: ClaudeAdapter) -> dict[str, Any]:
    return await adapter.on_event("pre_tool_use", {"tool_name": "Bash",
                                                   "tool_input": {"command": "ls"}})


class TestDenialText:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("action, code", [("block", "block"), ("escalate", "escalate"),
                                              ("crash", "error")])
    async def test_the_model_sees_a_stable_code_only(self, action: str, code: str) -> None:
        decision = await _pre_tool(ClaudeAdapter(**_kwargs(action, enforce=True)))
        assert _denied(decision)
        reason = _reason(decision)
        assert reason.startswith(f"agentegrity.denied:{code}")
        for leak in ("score", "0.", "RuntimeError", "verdict layer", "detector"):
            assert leak not in reason


class TestOnDenyOption:
    def test_unknown_values_are_refused(self) -> None:
        with pytest.raises(ValueError, match="on_deny"):
            LangChainAdapter(profile=AgentProfile.default(name="t"), on_deny="stop")

    @pytest.mark.parametrize("module, name", [
        ("agentegrity.adapters.claude", "ClaudeAdapter"),
        ("agentegrity.adapters.crewai", "CrewAIAdapter"),
        ("agentegrity.adapters.google_adk", "GoogleADKAdapter"),
        ("agentegrity.adapters.autogen", "AutoGenAdapter"),
    ])
    def test_frameworks_that_cannot_stop_on_an_exception_refuse_raise(
        self, module: str, name: str,
    ) -> None:
        adapter_class = getattr(pytest.importorskip(module), name)
        with pytest.raises(ValueError, match="on_deny"):
            adapter_class(profile=AgentProfile.default(name="t"), on_deny="raise")


# --- LangChain create_agent middleware and LangGraph ToolNode wrappers ---


def _shell_tool(ran: list[str]) -> Any:
    from langchain_core.tools import tool

    @tool
    def run_shell(command: str) -> str:
        """Run a shell command."""
        ran.append(command)
        return "ok"

    return run_shell


def _scripted_model() -> Any:
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class _Scripted(GenericFakeChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

    return _Scripted(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "run_shell", "args": {"command": "ls"},
                                           "id": "c1", "type": "tool_call"}]),
        AIMessage(content="done"),
    ]))


def _tool_messages(result: dict[str, Any]) -> list[Any]:
    return [m for m in result["messages"] if m.type == "tool"]


@_needs("langchain")
class TestLangChainMiddleware:
    def _run(self, action: str, ran: list[str], *, on_deny: str = "continue",
             asynchronous: bool = False, also_callback: bool = False) -> tuple[Any, Any]:
        from langchain.agents import create_agent

        adapter = LangChainAdapter(**_kwargs(action, enforce=True), on_deny=on_deny)
        agent = create_agent(_scripted_model(), tools=[_shell_tool(ran)],
                             middleware=[adapter.middleware()])
        if also_callback:
            agent = adapter.instrument_graph(agent)
        state = {"messages": [{"role": "user", "content": "hi"}]}
        result = asyncio.run(agent.ainvoke(state)) if asynchronous else agent.invoke(state)
        return adapter, result

    @pytest.mark.parametrize("asynchronous", [False, True])
    def test_a_denied_call_returns_the_denial_and_the_run_finishes(
        self, asynchronous: bool,
    ) -> None:
        ran: list[str] = []
        _, result = self._run("block", ran, asynchronous=asynchronous)
        assert ran == []
        (message,) = _tool_messages(result)
        assert message.status == "error"
        assert str(message.content).startswith("agentegrity.denied:block")
        assert result["messages"][-1].content == "done"

    def test_an_allowed_call_runs(self) -> None:
        ran: list[str] = []
        _, result = self._run("pass", ran)
        assert ran == ["ls"]
        assert result["messages"][-1].content == "done"

    def test_raise_ends_the_run(self) -> None:
        with pytest.raises(IntegrityViolationError, match="agentegrity.denied:block"):
            self._run("block", [], on_deny="raise")

    def test_handing_out_wrappers_leaves_other_chains_gated(self) -> None:
        # Gating is skipped per call the wrapper handled, never adapter-wide.
        ran: list[str] = []
        adapter = LangChainAdapter(**_kwargs("block", enforce=True))
        adapter.tool_wrappers()
        with pytest.raises(IntegrityViolationError):
            adapter.instrument_chain(_shell_tool(ran)).invoke({"command": "ls"})
        assert ran == []

    def test_the_middleware_alone_records_tool_results(self) -> None:
        # The next gate needs earlier tool output to catch injection arriving in it.
        adapter, _ = self._run("pass", [])
        post = [e for e in adapter.events if e.event_type == "post_tool_use"]
        assert [e.data["tool_response"] for e in post] == ["ok"]

    def test_the_callback_handler_does_not_record_a_wrapped_call_twice(self) -> None:
        adapter, _ = self._run("pass", [], also_callback=True)
        kinds = [e.event_type for e in adapter.events]
        assert (kinds.count("pre_tool_use"), kinds.count("post_tool_use")) == (1, 1)

    def test_the_callback_handler_does_not_evaluate_the_call_twice(self) -> None:
        ran: list[str] = []
        adapter, result = self._run("block", ran, also_callback=True)
        assert ran == []
        assert result["messages"][-1].content == "done"
        assert [e.event_type for e in adapter.events].count("pre_tool_use") == 1


@_needs("langgraph")
class TestToolNodeWrappers:
    @staticmethod
    def _run(action: str, ran: list[str], *, asynchronous: bool = False) -> list[Any]:
        """Run one tool call through a graph whose only node is the wrapped ToolNode."""
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode

        adapter = LangChainAdapter(**_kwargs(action, enforce=True))
        builder = StateGraph(MessagesState)
        builder.add_node("tools", ToolNode([_shell_tool(ran)], **adapter.tool_wrappers()))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        state = {"messages": [AIMessage(content="", tool_calls=[
            {"name": "run_shell", "args": {"command": "ls"}, "id": "c1", "type": "tool_call"},
        ])]}
        result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)
        return _tool_messages(result)

    @pytest.mark.parametrize("asynchronous", [False, True])
    def test_a_denied_call_becomes_an_error_message(self, asynchronous: bool) -> None:
        ran: list[str] = []
        (message,) = self._run("block", ran, asynchronous=asynchronous)
        assert ran == []
        assert message.status == "error"
        assert str(message.content).startswith("agentegrity.denied:block")

    def test_an_allowed_call_runs(self) -> None:
        ran: list[str] = []
        (message,) = self._run("pass", ran)
        assert ran == ["ls"]
        assert message.content == "ok"


# --- OpenAI Agents SDK tool guardrails ---


def _openai_run(adapter: Any, agent: Any, *, hooks: bool = False) -> Any:
    from agents import Runner

    return asyncio.run(Runner.run(agent, "hi",
                                  hooks=adapter.create_run_hooks() if hooks else None))


def _openai_agent(ran: list[str], outputs: list[Any], name: str = "a",
                  tools: list[Any] | None = None) -> Any:
    from agents import Agent, function_tool
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage

    @function_tool
    def run_shell(command: str) -> str:
        """Run a shell command."""
        ran.append(command)
        return "ok"

    class _Scripted(Model):
        def __init__(self) -> None:
            self.inputs: list[Any] = []

        async def get_response(self, system_instructions: Any, input: Any,
                               *args: Any, **kwargs: Any) -> ModelResponse:
            self.inputs.append(input)
            return ModelResponse(output=[outputs.pop(0)], usage=Usage(), response_id=None)

        def stream_response(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError

    return Agent(name=name, model=_Scripted(),
                 tools=tools if tools is not None else [run_shell])


def _call(name: str, arguments: dict[str, Any]) -> Any:
    from openai.types.responses import ResponseFunctionToolCall

    return ResponseFunctionToolCall(type="function_call", call_id=f"c-{name}", name=name,
                                    arguments=json.dumps(arguments))


def _text(text: str) -> Any:
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    return ResponseOutputMessage(
        id="m1", type="message", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def _tool_output_seen_by(agent: Any) -> str:
    """The function_call_output the model received on its second turn."""
    second_turn = agent.model.inputs[1]
    outputs = [item for item in second_turn
               if isinstance(item, dict) and item.get("type") == "function_call_output"]
    return str(outputs[-1]["output"])


@_needs("agents")
class TestOpenAIGuardrails:
    def test_a_denied_call_returns_the_denial_and_the_run_finishes(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        ran: list[str] = []
        agent = _openai_agent(ran, [_call("run_shell", {"command": "ls"}), _text("done")])
        adapter = OpenAIAgentsAdapter(**_kwargs("block", enforce=True))
        result = _openai_run(adapter, adapter.instrument(agent))
        assert ran == []
        assert result.final_output == "done"
        assert _tool_output_seen_by(agent).startswith("agentegrity.denied:block")

    def test_an_allowed_call_runs(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        ran: list[str] = []
        agent = _openai_agent(ran, [_call("run_shell", {"command": "ls"}), _text("done")])
        adapter = OpenAIAgentsAdapter(**_kwargs("pass", enforce=True))
        assert _openai_run(adapter, adapter.instrument(agent)).final_output == "done"
        assert ran == ["ls"]

    def test_raise_ends_the_run(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        agent = _openai_agent([], [_call("run_shell", {"command": "ls"}), _text("done")])
        adapter = OpenAIAgentsAdapter(**_kwargs("block", enforce=True), on_deny="raise")
        with pytest.raises(IntegrityViolationError, match="agentegrity.denied:block"):
            _openai_run(adapter, adapter.instrument(agent))

    def test_run_hooks_do_not_evaluate_a_guarded_call_again(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        ran: list[str] = []
        agent = _openai_agent(ran, [_call("run_shell", {"command": "ls"}), _text("done")])
        adapter = OpenAIAgentsAdapter(**_kwargs("block", enforce=True))
        result = _openai_run(adapter, adapter.instrument(agent), hooks=True)
        assert result.final_output == "done"
        assert [e.event_type for e in adapter.events].count("pre_tool_use") == 1

    def test_tools_inside_an_agent_used_as_a_tool_are_gated(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        ran: list[str] = []
        inner = _openai_agent(ran, [_call("run_shell", {"command": "ls"}),
                                    _text("inner done")], name="inner")
        researcher = inner.as_tool(tool_name="researcher", tool_description="Research.")
        outer = _openai_agent([], [_call("researcher", {"input": "go"}), _text("done")],
                              name="outer", tools=[researcher])
        adapter = OpenAIAgentsAdapter(profile=AgentProfile.default(name="t"), enforce=True,
                                      evaluator=IntegrityEvaluator(layers=[_Blocks("run_shell")]))
        result = _openai_run(adapter, adapter.instrument(outer))
        assert ran == []
        assert result.final_output == "done"
        assert _tool_output_seen_by(inner).startswith("agentegrity.denied:block")

    def test_agents_wrapped_in_handoff_are_gated(self) -> None:
        from agents import handoff

        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        billing = _openai_agent([], [], name="billing")
        triage = _openai_agent([], [], name="triage", tools=[])
        triage.handoffs = [handoff(billing)]
        adapter = OpenAIAgentsAdapter(**_kwargs("pass", enforce=True))
        adapter.instrument(triage)
        if getattr(triage.handoffs[0], "_agent_ref", None) is None:
            pytest.skip("this SDK version keeps no reference to a handoff's agent")
        assert adapter._guards(billing.tools[0])

    def test_instrumenting_twice_attaches_one_guardrail(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        agent = _openai_agent([], [])
        adapter = OpenAIAgentsAdapter(**_kwargs("pass", enforce=True))
        adapter.instrument(adapter.instrument(agent))
        assert len(agent.tools[0].tool_input_guardrails) == 1

    def test_an_sdk_without_agent_tool_access_warns(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agentegrity.adapters import openai_agents as module

        monkeypatch.setattr(module, "_reaches_agent_tools", lambda: False)
        adapter = module.OpenAIAgentsAdapter(**_kwargs("pass", enforce=True))
        with pytest.warns(UserWarning, match="as_tool"):
            adapter.instrument(_openai_agent([], []))


# --- Google ADK plugin ---


@_needs("google.adk")
class TestADKPlugin:
    @staticmethod
    def _run(action: str, ran: list[str], *, via: str) -> tuple[Any, list[dict[str, Any]]]:
        from google.adk.agents import LlmAgent
        from google.adk.apps import App
        from google.adk.models.base_llm import BaseLlm
        from google.adk.models.llm_request import LlmRequest
        from google.adk.models.llm_response import LlmResponse
        from google.adk.runners import InMemoryRunner, Runner
        from google.adk.sessions import InMemorySessionService
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
        adapter = GoogleADKAdapter(**_kwargs(action, enforce=True))
        if via == "app":
            app = adapter.instrument(App(name="app", root_agent=agent))
            runner = Runner(app=app, session_service=InMemorySessionService())
        else:
            runner = adapter.instrument(InMemoryRunner(agent=agent, app_name="app"))

        async def main() -> None:
            session = await runner.session_service.create_session(app_name="app", user_id="u")
            message = types.Content(role="user", parts=[types.Part(text="hi")])
            async for _ in runner.run_async(user_id="u", session_id=session.id,
                                            new_message=message):
                pass

        asyncio.run(main())
        return adapter, tool_results

    @pytest.mark.parametrize("via", ["runner", "app"])
    def test_the_plugin_denies_and_the_run_continues(self, via: str) -> None:
        ran: list[str] = []
        adapter, results = self._run("block", ran, via=via)
        assert ran == []
        assert str(results[0]["error"]).startswith("agentegrity.denied:block")
        kinds = [e.event_type for e in adapter.events]
        assert kinds.count("pre_tool_use") == 1
        # The denial handed to the model is not recorded as a tool result.
        assert "post_tool_use" not in kinds

    def test_a_workflow_tool_node_is_gated(self) -> None:
        from google.adk.runners import InMemoryRunner
        from google.adk.tools import FunctionTool
        from google.adk.workflow import START, Workflow
        from google.genai import types

        from agentegrity.adapters.google_adk import GoogleADKAdapter

        ran: list[str] = []

        def run_shell(command: str = "ls") -> str:
            """Run a shell command."""
            ran.append(command)
            return "ok"

        runner = InMemoryRunner(node=Workflow(name="wf", edges=[(START, FunctionTool(run_shell))]),
                                app_name="app")
        adapter = GoogleADKAdapter(**_kwargs("block", enforce=True))
        adapter.instrument(runner)

        async def main() -> list[Any]:
            session = await runner.session_service.create_session(app_name="app", user_id="u")
            message = types.Content(role="user", parts=[types.Part(text='{"command": "ls"}')])
            return [getattr(event, "output", None) async for event in runner.run_async(
                user_id="u", session_id=session.id, new_message=message)]

        outputs = asyncio.run(main())
        assert ran == []
        assert any(isinstance(o, dict) and str(o.get("error", "")).startswith(
            "agentegrity.denied:block") for o in outputs)

    def test_an_allowed_call_runs(self) -> None:
        ran: list[str] = []
        self._run("pass", ran, via="runner")
        assert ran == ["ls"]
