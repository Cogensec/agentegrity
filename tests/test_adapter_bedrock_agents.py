"""Live tests for BedrockAgentsAdapter.

Covers both surfaces:

* **Strands path**: a real ``HookRegistry`` receives our hook provider;
  synthesized ``BeforeInvocationEvent`` / ``BeforeToolCallEvent`` etc.
  drive the registry. No model or AWS call is made.
* **boto3 path**: a fake boto3 client whose ``invoke_agent`` returns a
  hand-rolled ``completion`` iterator with the same TracePart shapes
  the real service emits. No AWS call is made.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator

import pytest

pytest.importorskip("strands")
pytest.importorskip("boto3")

from strands.hooks import HookRegistry
from strands.hooks.events import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
)

from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter
from agentegrity.core.profile import AgentProfile


def _profile() -> AgentProfile:
    return AgentProfile.default()


class _FakeAgent:
    """Duck-typed stand-in for strands.Agent — only `name` and `.hooks` matter."""

    def __init__(self) -> None:
        self.name = "test-agent"
        self.hooks = HookRegistry()


# ----- Strands path -----


def _drive_registry(registry: HookRegistry, event: Any) -> Any:
    """Run async callbacks the same way Strands does in production."""
    loop = asyncio.new_event_loop()
    try:
        _, _ = loop.run_until_complete(registry.invoke_callbacks_async(event))
    finally:
        loop.close()
    return event


def test_strands_invocation_lifecycle_emits_prompt_and_stop() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    agent = _FakeAgent()
    adapter.instrument_strands(agent)  # type: ignore[arg-type]

    before = BeforeInvocationEvent(
        agent=agent,  # type: ignore[arg-type]
        invocation_state={},
        messages=[{"role": "user", "content": [{"text": "find files"}]}],
    )
    _drive_registry(agent.hooks, before)
    after = AfterInvocationEvent(agent=agent, invocation_state={}, result=None)  # type: ignore[arg-type]
    _drive_registry(agent.hooks, after)

    types = [e.event_type for e in adapter.events]
    assert types == ["user_prompt_submit", "stop"]
    assert adapter.events[0].data["prompt"] == "find files"


def test_strands_tool_lifecycle_success_emits_pre_and_post() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    agent = _FakeAgent()
    adapter.instrument_strands(agent)  # type: ignore[arg-type]

    tool_use = {"toolUseId": "t1", "name": "search", "input": {"q": "x"}}
    before = BeforeToolCallEvent(
        agent=agent,  # type: ignore[arg-type]
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
    )
    _drive_registry(agent.hooks, before)
    after = AfterToolCallEvent(
        agent=agent,  # type: ignore[arg-type]
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
        result={"status": "success", "content": [{"text": "ok"}]},
    )
    _drive_registry(agent.hooks, after)

    types = [e.event_type for e in adapter.events]
    assert types == ["pre_tool_use", "post_tool_use"]
    assert adapter.events[0].data["tool_name"] == "search"


def test_strands_tool_failure_emits_post_tool_use_failure() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    agent = _FakeAgent()
    adapter.instrument_strands(agent)  # type: ignore[arg-type]

    tool_use = {"toolUseId": "t2", "name": "broken", "input": {}}
    _drive_registry(
        agent.hooks,
        BeforeToolCallEvent(
            agent=agent,  # type: ignore[arg-type]
            selected_tool=None,
            tool_use=tool_use,
            invocation_state={},
        ),
    )
    _drive_registry(
        agent.hooks,
        AfterToolCallEvent(
            agent=agent,  # type: ignore[arg-type]
            selected_tool=None,
            tool_use=tool_use,
            invocation_state={},
            result={"status": "error", "content": []},
            exception=RuntimeError("kaboom"),
        ),
    )

    types = [e.event_type for e in adapter.events]
    assert types == ["pre_tool_use", "post_tool_use_failure"]
    failure = adapter.events[-1]
    assert "kaboom" in failure.data["error"]


def test_strands_enforce_true_writes_cancel_tool_on_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """When enforce=True and the evaluator blocks, the adapter must set
    event.cancel_tool so Strands refuses to execute the tool.

    We force a deny by stubbing the base handler to return a deny dict.
    """
    adapter = BedrockAgentsAdapter(profile=_profile(), enforce=True)
    agent = _FakeAgent()
    adapter.instrument_strands(agent)  # type: ignore[arg-type]

    async def fake_on_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        if event_type == "pre_tool_use":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "test block",
                }
            }
        return {}

    monkeypatch.setattr(adapter, "on_event", fake_on_event)

    tool_use = {"toolUseId": "t3", "name": "dangerous", "input": {}}
    event = BeforeToolCallEvent(
        agent=agent,  # type: ignore[arg-type]
        selected_tool=None,
        tool_use=tool_use,
        invocation_state={},
    )
    _drive_registry(agent.hooks, event)

    assert event.cancel_tool == "test block"


# ----- boto3 path -----


def _trace_part(trace_body: dict[str, Any]) -> dict[str, Any]:
    """Build a TracePart event matching the wire shape."""
    return {"trace": {"trace": trace_body}}


def _make_fake_client(stream: Iterator[dict[str, Any]]) -> Any:
    """A boto3-shaped stand-in: only `invoke_agent` is exercised."""
    calls: list[dict[str, Any]] = []

    def invoke_agent(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"completion": iter(stream), "contentType": "text/event-stream"}

    class _Client:
        pass

    c = _Client()
    c.invoke_agent = invoke_agent  # type: ignore[attr-defined]
    c._calls = calls  # type: ignore[attr-defined]
    return c


def test_wrap_client_forces_enable_trace_by_default() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    client = _make_fake_client(iter([]))
    wrapped = adapter.wrap_client(client)

    resp = wrapped.invoke_agent(
        agentId="a", agentAliasId="b", sessionId="s", inputText="hi"
    )
    # Drain the completion so the generator's finally fires.
    list(resp["completion"])

    assert wrapped._calls[0]["enableTrace"] is True


def test_wrap_client_force_trace_false_skips_injection_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    client = _make_fake_client(iter([]))
    wrapped = adapter.wrap_client(client, force_trace=False)
    resp = wrapped.invoke_agent(
        agentId="a", agentAliasId="b", sessionId="s", inputText="hi"
    )
    list(resp["completion"])

    # No enableTrace injection.
    assert "enableTrace" not in wrapped._calls[0]
    # Loud warning so the user knows nothing will fire.
    assert any("no agentegrity events will fire" in r.message for r in caplog.records)


def test_wrap_client_enforce_true_emits_warning() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile(), enforce=True)
    client = _make_fake_client(iter([]))
    with pytest.warns(UserWarning, match="observation-only"):
        adapter.wrap_client(client)


def test_boto3_trace_stream_maps_full_lifecycle() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    client = _make_fake_client(
        iter(
            [
                # pre_tool_use
                _trace_part(
                    {
                        "orchestrationTrace": {
                            "invocationInput": {
                                "actionGroupInvocationInput": {
                                    "actionGroupName": "files",
                                    "function": "list_dir",
                                    "parameters": [{"name": "path", "value": "/tmp"}],
                                }
                            }
                        }
                    }
                ),
                # post_tool_use
                _trace_part(
                    {
                        "orchestrationTrace": {
                            "observation": {
                                "actionGroupInvocationOutput": {"text": "a.txt b.txt"}
                            }
                        }
                    }
                ),
                # chunk variant — must pass through untouched
                {"chunk": {"bytes": b"final answer"}},
            ]
        )
    )
    wrapped = adapter.wrap_client(client)

    resp = wrapped.invoke_agent(
        agentId="a", agentAliasId="b", sessionId="s", inputText="list /tmp"
    )
    raw = list(resp["completion"])

    # Caller still sees the chunk variant.
    assert raw[-1] == {"chunk": {"bytes": b"final answer"}}

    types = [e.event_type for e in adapter.events]
    assert types == ["user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"]
    pre = adapter.events[1]
    assert pre.data["tool_name"] == "list_dir"


def test_boto3_failure_trace_emits_post_tool_use_failure() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    client = _make_fake_client(
        iter(
            [
                _trace_part({"failureTrace": {"failureCode": 500, "failureReason": "kaboom"}}),
            ]
        )
    )
    wrapped = adapter.wrap_client(client)
    resp = wrapped.invoke_agent(
        agentId="a", agentAliasId="b", sessionId="s", inputText="x"
    )
    list(resp["completion"])

    types = [e.event_type for e in adapter.events]
    assert types == [
        "user_prompt_submit",
        "post_tool_use_failure",
        "stop",
    ]
    assert adapter.events[1].data["error"] == "kaboom"


def test_boto3_collaborator_invocations_emit_subagent_events() -> None:
    adapter = BedrockAgentsAdapter(profile=_profile())
    client = _make_fake_client(
        iter(
            [
                _trace_part(
                    {
                        "orchestrationTrace": {
                            "invocationInput": {
                                "agentCollaboratorInvocationInput": {
                                    "agentCollaboratorName": "Researcher",
                                }
                            }
                        }
                    }
                ),
                _trace_part(
                    {
                        "orchestrationTrace": {
                            "observation": {
                                "agentCollaboratorInvocationOutput": {
                                    "agentCollaboratorName": "Researcher",
                                }
                            }
                        }
                    }
                ),
            ]
        )
    )
    wrapped = adapter.wrap_client(client)
    resp = wrapped.invoke_agent(
        agentId="a", agentAliasId="b", sessionId="s", inputText="x"
    )
    list(resp["completion"])

    types = [e.event_type for e in adapter.events]
    assert types == [
        "user_prompt_submit",
        "subagent_start",
        "subagent_stop",
        "stop",
    ]


def test_boto3_partial_stream_still_fires_stop() -> None:
    """A caller bailing out mid-iteration must not strand the session."""
    adapter = BedrockAgentsAdapter(profile=_profile())
    client = _make_fake_client(
        iter(
            [
                {"chunk": {"bytes": b"1"}},
                {"chunk": {"bytes": b"2"}},
                {"chunk": {"bytes": b"3"}},
            ]
        )
    )
    wrapped = adapter.wrap_client(client)
    resp = wrapped.invoke_agent(agentId="a", agentAliasId="b", sessionId="s", inputText="x")
    completion = resp["completion"]
    # Consume only the first chunk, then drop the iterator.
    next(iter(completion))
    completion.close()

    types = [e.event_type for e in adapter.events]
    # user_prompt_submit at start, stop fires from the generator's finally
    # with the "stream_terminated_early" reason.
    assert types == ["user_prompt_submit", "stop"]
    stop = adapter.events[-1]
    assert stop.data.get("reason") == "stream_terminated_early"
