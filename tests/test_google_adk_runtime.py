"""Runs real Google ADK agents with a scripted offline model, so these tests
fail if the adapter stops receiving ADK's callbacks or breaks the run."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

pytest.importorskip("google.adk")

from google.adk.agents import LlmAgent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.adk.tools import AgentTool  # noqa: E402
from google.genai import types  # noqa: E402

from agentegrity.adapters.google_adk import GoogleADKAdapter  # noqa: E402
from agentegrity.core.profile import AgentProfile  # noqa: E402


class _Scripted(BaseLlm):
    """Replays one response per model call."""

    replies: list[LlmResponse] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self.replies.pop(0)


def _text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


def _call(name: str, args: dict[str, Any]) -> LlmResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[part]))


def _model(*replies: LlmResponse) -> _Scripted:
    return _Scripted(model="scripted", replies=list(replies))


def _adapter() -> tuple[GoogleADKAdapter, list[tuple[str, dict[str, Any]]]]:
    adapter = GoogleADKAdapter(profile=AgentProfile.default(name="t"))
    seen: list[tuple[str, dict[str, Any]]] = []
    dispatch = adapter._dispatch

    def record(event_type: str, data: dict[str, Any]) -> None:
        seen.append((event_type, data))
        dispatch(event_type, data)

    adapter._dispatch = record  # type: ignore[method-assign]
    return adapter, seen


def _run(agent: Any) -> str:
    async def main() -> str:
        runner = InMemoryRunner(agent=agent, app_name="app")
        session = await runner.session_service.create_session(app_name="app", user_id="u")
        final = ""
        message = types.Content(role="user", parts=[types.Part(text="hi")])
        async for event in runner.run_async(user_id="u", session_id=session.id,
                                            new_message=message):
            parts = event.content.parts if event.content and event.content.parts else []
            final = next((p.text for p in parts if p.text), final)
        return final

    return asyncio.run(main())


def _types(seen: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [event_type for event_type, _ in seen]


def test_a_transfer_reports_the_sub_agent_as_a_sub_agent() -> None:
    beta = LlmAgent(name="beta", description="helper", model=_model(_text("from beta")))
    alpha = LlmAgent(name="alpha", model=_model(_call("transfer_to_agent", {"agent_name": "beta"})),
                     sub_agents=[beta])
    adapter, seen = _adapter()
    adapter.instrument(alpha)
    _run(alpha)
    # ADK does not run the root's after-agent callbacks once it has transferred,
    # so a run that ends in a transfer reports no stop.
    assert _types(seen).count("user_prompt_submit") == 1
    assert ("subagent_start", {"agent_id": "beta"}) in seen
    assert ("subagent_stop", {"agent_id": "beta"}) in seen


def test_an_agent_wrapped_as_a_tool_is_a_sub_agent() -> None:
    gamma = LlmAgent(name="gamma", description="helper", model=_model(_text("from gamma")))
    alpha = LlmAgent(name="alpha", tools=[AgentTool(agent=gamma)],
                     model=_model(_call("gamma", {"request": "go"}), _text("done")))
    adapter, seen = _adapter()
    adapter.instrument(alpha)
    _run(alpha)
    assert ("subagent_start", {"agent_id": "gamma"}) in seen
    assert _types(seen).count("user_prompt_submit") == 1


def test_workflow_agents_are_instrumented_with_their_sub_agents() -> None:
    from google.adk.agents import SequentialAgent

    first = LlmAgent(name="first", model=_model(_text("one")))
    second = LlmAgent(name="second", model=_model(_text("two")))
    flow = SequentialAgent(name="flow", sub_agents=[first, second])
    adapter, seen = _adapter()
    adapter.instrument(flow)
    _run(flow)
    assert [e for e in _types(seen) if e != "topology_declared"] == [
        "user_prompt_submit", "subagent_start", "subagent_stop",
        "subagent_start", "subagent_stop", "stop"]


def test_list_callbacks_still_run_and_still_decide() -> None:
    calls: list[str] = []

    def before(callback_context: Any) -> None:
        calls.append("before agent")

    def after_model(callback_context: Any, llm_response: Any) -> LlmResponse:
        calls.append("after model")
        return _text("replaced")

    alpha = LlmAgent(name="alpha", model=_model(_text("original")),
                     before_agent_callback=[before], after_model_callback=[after_model])
    adapter, seen = _adapter()
    adapter.instrument(alpha)
    assert _run(alpha) == "replaced"
    assert calls == ["before agent", "after model"]
    assert _types(seen) == ["user_prompt_submit", "stop"]


def test_instrumenting_twice_reports_each_event_once() -> None:
    alpha = LlmAgent(name="alpha", model=_model(_text("hi")))
    adapter, seen = _adapter()
    adapter.instrument(alpha)
    adapter.instrument(alpha)
    _run(alpha)
    assert _types(seen) == ["user_prompt_submit", "stop"]


def test_an_adapter_failure_does_not_break_the_run() -> None:
    alpha = LlmAgent(name="alpha", model=_model(_text("ok")),
                     before_agent_callback=[lambda callback_context: None])
    adapter, _ = _adapter()

    def boom(event_type: str, data: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    adapter.instrument(alpha)
    adapter._dispatch = boom  # type: ignore[method-assign]
    assert _run(alpha) == "ok"
