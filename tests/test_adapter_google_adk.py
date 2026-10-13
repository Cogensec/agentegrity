"""Tests for the Google ADK adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agentegrity.adapters.google_adk import GoogleADKAdapter
from agentegrity.core.profile import AgentProfile


class _FakeAgent:
    before_agent_callback: Any = None
    after_agent_callback: Any = None
    before_tool_callback: Any = None
    after_tool_callback: Any = None


def test_adapter_name() -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default())
    assert ad.name == "google_adk"


def test_before_tool_returns_the_denial_under_enforcement() -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default(), enforce=True)

    async def deny(name: str, args: Any) -> str:
        return "denied"

    ad._atool_call_denial = deny  # type: ignore[method-assign]
    agent = _FakeAgent()
    ad.instrument(agent)
    result = asyncio.run(agent.before_tool_callback(SimpleNamespace(name="t"), {}, None))
    assert result == {"error": "denied"}


@pytest.mark.asyncio
async def test_on_event_user_prompt() -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default())
    await ad.on_event("user_prompt_submit", {"prompt": "analyze"})
    assert ad.get_collected_context()["input"] == "analyze"


def test_instrument_attaches_callbacks() -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default())
    agent = _FakeAgent()
    ad.instrument(agent)
    assert agent.before_agent_callback is not None
    assert agent.after_agent_callback is not None
    assert agent.before_tool_callback is not None
    assert agent.after_tool_callback is not None


def test_instrument_before_tool_fires_event() -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default())
    agent = _FakeAgent()
    ad.instrument(agent)

    tool = type("T", (), {"name": "search"})()
    asyncio.run(agent.before_tool_callback(tool, {"q": "llm"}, object()))
    ctx = ad.get_collected_context()
    assert ctx["tool_usage"]["search"] == 1


@pytest.mark.parametrize("user_is_async", [False, True])
def test_before_tool_runs_the_users_callback_after_an_allow(user_is_async: bool) -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default(), enforce=True)
    called: list[str] = []

    def user_sync(tool: Any, args: Any, ctx: Any) -> dict[str, str]:
        called.append("user")
        return {"cached": "yes"}

    async def user_async(tool: Any, args: Any, ctx: Any) -> dict[str, str]:
        return user_sync(tool, args, ctx)

    agent = _FakeAgent()
    agent.before_tool_callback = user_async if user_is_async else user_sync
    ad.instrument(agent)
    result = asyncio.run(agent.before_tool_callback(SimpleNamespace(name="t"), {}, None))
    assert result == {"cached": "yes"}
    assert called == ["user"]


def test_instrument_chains_existing_callback() -> None:
    ad = GoogleADKAdapter(profile=AgentProfile.default())
    called: list[str] = []

    class ExistingAgent:
        def before_agent_callback(self, cc: Any) -> None:
            called.append("user")

        after_agent_callback = None
        before_tool_callback = None
        after_tool_callback = None

    agent = ExistingAgent()
    ad.instrument(agent)
    cc = type("CC", (), {"parent": None, "user_content": "hi", "agent_name": "a"})()
    agent.before_agent_callback(cc)  # type: ignore[operator]
    assert "user" in called
    assert ad.evaluation_count >= 1
