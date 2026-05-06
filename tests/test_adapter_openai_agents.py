"""Tests for the OpenAI Agents SDK adapter."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter
from agentegrity.core.profile import AgentProfile


@pytest.fixture
def stub_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("agents")

    class RunHooks:
        async def on_agent_start(self, context: Any, agent: Any) -> None: ...
        async def on_agent_end(
            self, context: Any, agent: Any, output: Any
        ) -> None: ...
        async def on_tool_start(
            self, context: Any, agent: Any, tool: Any
        ) -> None: ...
        async def on_tool_end(
            self, context: Any, agent: Any, tool: Any, result: Any
        ) -> None: ...
        async def on_handoff(
            self, context: Any, from_agent: Any, to_agent: Any
        ) -> None: ...

    mod.RunHooks = RunHooks  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents", mod)


def test_adapter_name() -> None:
    ad = OpenAIAgentsAdapter(profile=AgentProfile.default())
    assert ad.name == "openai_agents"


@pytest.mark.asyncio
async def test_on_event_user_prompt() -> None:
    ad = OpenAIAgentsAdapter(profile=AgentProfile.default())
    await ad.on_event("user_prompt_submit", {"prompt": "hi"})
    assert ad.get_collected_context()["input"] == "hi"


@pytest.mark.asyncio
async def test_run_hooks_forwards_to_on_event(stub_agents: None) -> None:
    ad = OpenAIAgentsAdapter(profile=AgentProfile.default())
    hooks = ad.create_run_hooks()

    ctx = types.SimpleNamespace(input="research llms")
    await hooks.on_agent_start(ctx, object())
    assert ad.get_collected_context()["input"] == "research llms"

    tool = types.SimpleNamespace(name="search")
    await hooks.on_tool_start(ctx, object(), tool)
    assert ad.get_collected_context()["tool_usage"]["search"] == 1

    await hooks.on_tool_end(ctx, object(), tool, "results")
    await hooks.on_agent_end(ctx, object(), "final")
    assert ad.evaluation_count >= 3


def test_create_run_hooks_requires_agents_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force import failure even when openai-agents is installed
    # (the coverage CI job installs [all,dev]).
    monkeypatch.setitem(sys.modules, "agents", None)
    ad = OpenAIAgentsAdapter(profile=AgentProfile.default())
    with pytest.raises(ImportError, match="openai-agents"):
        ad.create_run_hooks()
