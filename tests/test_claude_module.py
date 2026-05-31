"""Tests for the agentegrity.claude zero-config surface."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import agentegrity.claude as ac
from agentegrity.core.profile import AgentProfile, AgentType


@pytest.fixture
def stub_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mod = types.ModuleType("claude_agent_sdk")

    class HookMatcher:
        def __init__(
            self, hooks: list[Any] | None = None, matcher: str | None = None
        ) -> None:
            self.hooks = hooks or []
            self.matcher = matcher

    fake_mod.HookMatcher = HookMatcher  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_mod)


@pytest.fixture(autouse=True)
def _clean_default() -> None:
    ac.reset()
    yield
    ac.reset()


def test_profile_default_roundtrip() -> None:
    profile = AgentProfile.default()
    assert profile.agent_type == AgentType.TOOL_USING
    assert "tool_use" in profile.capabilities
    assert profile.name == "claude-agent"


def test_hooks_zero_config_returns_matchers(stub_sdk: None) -> None:
    h = ac.hooks()
    assert "PreToolUse" in h
    assert "PostToolUse" in h
    assert "Stop" in h
    assert "UserPromptSubmit" in h
    assert "SubagentStart" in h
    assert "PreCompact" in h


def test_hooks_reuses_default_adapter(stub_sdk: None) -> None:
    ac.hooks()
    first = ac.adapter()
    ac.hooks()
    second = ac.adapter()
    assert first is second


def test_hooks_with_explicit_profile_does_not_touch_global(stub_sdk: None) -> None:
    custom = AgentProfile.default(name="custom")
    ac.hooks(profile=custom)
    # Module global should still be unset after an explicit-config call.
    assert ac._default is None


def test_report_before_hooks_returns_empty() -> None:
    summary = ac.report()
    assert summary["adapter"] == "claude"
    assert summary["evaluations"] == 0
    assert summary["attestation_records"] == 0
    assert summary["chain_valid"] is True
    assert summary["agent_id"] is None


@pytest.mark.asyncio
async def test_reset_isolates_sessions(stub_sdk: None) -> None:
    ac.hooks()
    ad = ac.adapter()
    await ad.on_event("user_prompt_submit", {"prompt": "hello"})
    await ad.on_event("stop", {})
    assert ac.report()["events"] >= 2

    ac.reset()
    fresh = ac.report()
    assert fresh["evaluations"] == 0
    assert fresh["events"] == 0


def test_module_hooks_equivalent_to_low_level(stub_sdk: None) -> None:
    """The zero-config hooks() must return the same shape as the
    low-level ClaudeAdapter.create_hooks() — proving the default path
    is a thin wrapper and not a reimplementation."""
    from agentegrity import AgentegrityClient

    client = AgentegrityClient()
    low_level = client.create_adapter(
        "claude", profile=AgentProfile.default()
    ).create_hooks()
    high_level = ac.hooks()
    assert set(high_level.keys()) == set(low_level.keys())
