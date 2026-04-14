"""Tests for the Claude Agent SDK adapter."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)


def _make_profile() -> AgentProfile:
    return AgentProfile(
        agent_type=AgentType.TOOL_USING,
        capabilities=["web_search"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
        name="test-agent",
    )


@pytest.fixture
def adapter() -> ClaudeAdapter:
    return ClaudeAdapter(profile=_make_profile())


def test_adapter_name_and_profile(adapter: ClaudeAdapter) -> None:
    assert adapter.name == "claude"
    assert adapter.profile.name == "test-agent"
    assert adapter.evaluation_count == 0


@pytest.mark.asyncio
async def test_user_prompt_submit_records_input(adapter: ClaudeAdapter) -> None:
    await adapter.on_event("user_prompt_submit", {"prompt": "hello world"})
    ctx = adapter.get_collected_context()
    assert ctx["input"] == "hello world"
    assert adapter.evaluation_count == 1


@pytest.mark.asyncio
async def test_pre_tool_use_tracks_call(adapter: ClaudeAdapter) -> None:
    await adapter.on_event(
        "pre_tool_use",
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    )
    ctx = adapter.get_collected_context()
    assert ctx["tool_usage"]["Bash"] == 1
    assert ctx["action_distribution"]["tool_call"] == 1
    assert adapter.evaluation_count == 1


@pytest.mark.asyncio
async def test_post_tool_use_records_output(adapter: ClaudeAdapter) -> None:
    await adapter.on_event(
        "post_tool_use",
        {"tool_name": "Bash", "tool_response": "file.txt"},
    )
    ctx = adapter.get_collected_context()
    assert len(ctx["tool_outputs"]) == 1
    assert ctx["tool_outputs"][0]["output"] == "file.txt"


@pytest.mark.asyncio
async def test_enforce_mode_denies_on_block(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ClaudeAdapter(profile=_make_profile(), enforce=True)

    class _FakeScore:
        action = "block"
        composite = 0.1

        def to_dict(self) -> dict[str, Any]:
            return {"composite": 0.1, "action": "block"}

        layer_results: list[Any] = []

    def _fake_eval(self: ClaudeAdapter, context: Any = None) -> Any:
        self._evaluation_count += 1
        return _FakeScore()

    monkeypatch.setattr(ClaudeAdapter, "_run_evaluation", _fake_eval)

    result = await adapter.on_event(
        "pre_tool_use",
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
    )
    assert "hookSpecificOutput" in result
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_measure_only_mode_never_blocks(adapter: ClaudeAdapter) -> None:
    # default enforce=False
    result = await adapter.on_event(
        "pre_tool_use",
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    )
    assert result == {}


@pytest.mark.asyncio
async def test_stop_triggers_evaluation(adapter: ClaudeAdapter) -> None:
    await adapter.on_event("stop", {})
    assert adapter.evaluation_count == 1
    assert len(adapter.events) == 1
    assert adapter.events[0].event_type == "stop"


@pytest.mark.asyncio
async def test_subagent_lifecycle(adapter: ClaudeAdapter) -> None:
    await adapter.on_event("subagent_start", {"agent_id": "sub-1"})
    await adapter.on_event(
        "subagent_stop",
        {"agent_id": "sub-1", "agent_transcript_path": "/tmp/t.jsonl"},
    )
    types_seen = [e.event_type for e in adapter.events]
    assert "subagent_start" in types_seen
    assert "subagent_stop" in types_seen


@pytest.mark.asyncio
async def test_attestation_chain_builds(adapter: ClaudeAdapter) -> None:
    await adapter.on_event("user_prompt_submit", {"prompt": "hi"})
    await adapter.on_event("stop", {})
    assert len(adapter.attestation_chain.records) == 2
    assert adapter.attestation_chain.verify_chain()


@pytest.mark.asyncio
async def test_hook_handler_exceptions_swallowed(
    adapter: ClaudeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(self: ClaudeAdapter, context: Any = None) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(ClaudeAdapter, "_run_evaluation", _boom)
    # Should not raise
    result = await adapter.on_event("stop", {})
    assert result == {}


def test_get_summary(adapter: ClaudeAdapter) -> None:
    summary = adapter.get_summary()
    assert summary["adapter"] == "claude"
    assert summary["evaluations"] == 0
    assert summary["enforce_mode"] is False
    assert "chain_valid" in summary


def test_create_hooks_with_stub_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_hooks() should work when claude_agent_sdk is importable."""
    fake_mod = types.ModuleType("claude_agent_sdk")

    class HookMatcher:
        def __init__(self, hooks: list[Any] | None = None, matcher: str | None = None) -> None:
            self.hooks = hooks or []
            self.matcher = matcher

    fake_mod.HookMatcher = HookMatcher  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_mod)

    adapter = ClaudeAdapter(profile=_make_profile())
    hooks = adapter.create_hooks()
    assert "PreToolUse" in hooks
    assert "PostToolUse" in hooks
    assert "Stop" in hooks
    assert "SubagentStart" in hooks
    assert "PreCompact" in hooks


def test_create_hooks_missing_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_hooks() raises ImportError with install hint if SDK not present."""
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    adapter = ClaudeAdapter(profile=_make_profile())
    with pytest.raises(ImportError, match="claude-agent-sdk"):
        adapter.create_hooks()
