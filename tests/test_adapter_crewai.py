"""Tests for the CrewAI adapter."""

from __future__ import annotations

import sys
import types
from typing import Any, Callable

import pytest

from agentegrity.adapters.crewai import CrewAIAdapter
from agentegrity.core.profile import AgentProfile


class _FakeBus:
    """Minimal stand-in for crewai_event_bus: ``.on(EventClass)(fn)``."""

    def __init__(self) -> None:
        self.handlers: dict[type, list[Callable[[Any, Any], None]]] = {}

    def on(
        self, event_class: type
    ) -> Callable[[Callable[[Any, Any], None]], Callable[[Any, Any], None]]:
        def register(
            cb: Callable[[Any, Any], None],
        ) -> Callable[[Any, Any], None]:
            self.handlers.setdefault(event_class, []).append(cb)
            return cb

        return register

    def emit(self, source: Any, event: Any) -> None:
        for cb in self.handlers.get(type(event), []):
            cb(source, event)


@pytest.fixture
def stub_crewai_events(monkeypatch: pytest.MonkeyPatch) -> _FakeBus:
    """Inject a fake ``crewai.events`` module with stub event classes."""

    pkg = types.ModuleType("crewai")
    pkg.__path__ = []  # type: ignore[attr-defined]
    events = types.ModuleType("crewai.events")

    class CrewKickoffStartedEvent: ...

    class CrewKickoffCompletedEvent: ...

    class TaskStartedEvent: ...

    class ToolUsageStartedEvent: ...

    class ToolUsageFinishedEvent: ...

    class ToolUsageErrorEvent: ...

    bus = _FakeBus()

    events.CrewKickoffStartedEvent = CrewKickoffStartedEvent  # type: ignore[attr-defined]
    events.CrewKickoffCompletedEvent = CrewKickoffCompletedEvent  # type: ignore[attr-defined]
    events.TaskStartedEvent = TaskStartedEvent  # type: ignore[attr-defined]
    events.ToolUsageStartedEvent = ToolUsageStartedEvent  # type: ignore[attr-defined]
    events.ToolUsageFinishedEvent = ToolUsageFinishedEvent  # type: ignore[attr-defined]
    events.ToolUsageErrorEvent = ToolUsageErrorEvent  # type: ignore[attr-defined]
    events.crewai_event_bus = bus  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "crewai", pkg)
    monkeypatch.setitem(sys.modules, "crewai.events", events)
    return bus


def test_adapter_name() -> None:
    ad = CrewAIAdapter(profile=AgentProfile.default())
    assert ad.name == "crewai"


@pytest.mark.asyncio
async def test_on_event_user_prompt_and_tool() -> None:
    ad = CrewAIAdapter(profile=AgentProfile.default())
    await ad.on_event("user_prompt_submit", {"prompt": "do research"})
    await ad.on_event(
        "pre_tool_use", {"tool_name": "search", "tool_input": {"args": "llm"}}
    )
    ctx = ad.get_collected_context()
    assert ctx["input"] == "do research"
    assert ctx["tool_usage"]["search"] == 1


def test_subscribe_routes_bus_events(stub_crewai_events: _FakeBus) -> None:
    ad = CrewAIAdapter(profile=AgentProfile.default())
    ad.subscribe()

    events_mod = sys.modules["crewai.events"]

    kickoff = events_mod.CrewKickoffStartedEvent()
    kickoff.inputs = "investigate X"  # type: ignore[attr-defined]
    stub_crewai_events.emit(None, kickoff)

    tool_start = events_mod.ToolUsageStartedEvent()
    tool_start.tool_name = "search"  # type: ignore[attr-defined]
    tool_start.tool_args = {"q": "llm"}  # type: ignore[attr-defined]
    stub_crewai_events.emit(None, tool_start)

    tool_err = events_mod.ToolUsageErrorEvent()
    tool_err.tool_name = "search"  # type: ignore[attr-defined]
    tool_err.error = RuntimeError("boom")  # type: ignore[attr-defined]
    stub_crewai_events.emit(None, tool_err)

    ctx = ad.get_collected_context()
    assert ctx["input"] == "investigate X"
    assert ctx["tool_usage"]["search"] == 1
    failures = ad._buffer.tool_failures
    assert any(
        f.get("tool") == "search" and "boom" in f.get("error", "")
        for f in failures
    )


def test_subscribe_requires_crewai(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force import failure even when crewai is installed via [all].
    monkeypatch.setitem(sys.modules, "crewai.events", None)
    ad = CrewAIAdapter(profile=AgentProfile.default())
    with pytest.raises(ImportError, match="crewai"):
        ad.subscribe()
