"""Tests for Phase 4: adapter uplift to declare AgentTopology.

Covers Agno teams (instrument_team) and CrewAI crews (subscribe with
crew). Each adapter must construct an AgentTopology from the
framework primitive and call set_topology() so the chain commits to
the structure via Evidence(evidence_type="topology") and the layers
see the topology in their context.

CrewAI gets a behavioral-change test: tasks no longer map to
subagent_start (they map to task_started), and legacy_task_mapping=True
restores the v0.7 behavior with a DeprecationWarning.
"""

from __future__ import annotations

import sys
import types
import warnings
from typing import Any

import pytest

from agentegrity.adapters.crewai import CrewAIAdapter
from agentegrity.core.attestation import AttestationRecord
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.core.topology import (
    AgentRole,
    TopologyKind,
)


def _profile():
    return AgentProfile(
        name="phase4",
        agent_type=AgentType.MULTI_AGENT,
        capabilities=["tool_use", "multi_agent_comm"],
        deployment_context=DeploymentContext.MULTI_AGENT,
        risk_tier=RiskTier.MEDIUM,
    )


class _FakeAgnoTeam:
    """Minimal Agno Team duck-type with members + pre/post/tool hooks."""

    def __init__(self, name="leader", members=None):
        self.name = name
        self.members = members or []
        self.pre_hooks: list[Any] = []
        self.post_hooks: list[Any] = []
        self.tool_hooks: list[Any] = []


class _FakeAgnoMember:
    def __init__(self, name):
        self.name = name
        self.pre_hooks: list[Any] = []
        self.post_hooks: list[Any] = []
        self.tool_hooks: list[Any] = []


class TestAgnoInstrumentTeamDeclaresTopology:
    def test_static_members_declare_hub_spoke_topology(self):
        from agentegrity.adapters.agno import AgnoAdapter

        team = _FakeAgnoTeam(
            name="research-team",
            members=[
                _FakeAgnoMember("researcher"),
                _FakeAgnoMember("writer"),
            ],
        )
        adapter = AgnoAdapter(profile=_profile())
        adapter.instrument_team(team)

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.HUB_SPOKE
        # 1 leader + 2 members
        assert len(topology.members) == 3
        leader = topology.leader()
        assert leader is not None
        assert leader.agent_id == "research-team"
        assert leader.role is AgentRole.LEADER
        # Members have parent_id == leader
        for m in topology.members:
            if m.role is AgentRole.MEMBER:
                assert m.parent_id == "research-team"

    def test_topology_declared_event_emitted(self):
        from agentegrity.adapters.agno import AgnoAdapter

        team = _FakeAgnoTeam(
            members=[_FakeAgnoMember("worker-1")],
        )
        adapter = AgnoAdapter(profile=_profile())
        adapter.instrument_team(team)

        events = [e for e in adapter.events if e.event_type == "topology_declared"]
        assert len(events) == 1
        assert events[0].data["topology"]["kind"] == "hub_spoke"

    def test_topology_evidence_on_attestation(self):
        from agentegrity.adapters.agno import AgnoAdapter

        team = _FakeAgnoTeam(
            members=[_FakeAgnoMember("worker-1")],
        )
        adapter = AgnoAdapter(profile=_profile())
        adapter.instrument_team(team)

        attestations = [
            r for r in adapter.attestation_chain.records
            if isinstance(r, AttestationRecord)
        ]
        assert len(attestations) >= 1
        topology_evidence = [
            e for e in attestations[0].evidence
            if e.evidence_type == "topology"
        ]
        assert len(topology_evidence) == 1

    def test_callable_members_provider_still_declares_leader_topology(self):
        """Per architectural review: even when members is a callable,
        declare a single-member (leader-only) topology so layers see
        at least something."""
        from agentegrity.adapters.agno import AgnoAdapter

        team = _FakeAgnoTeam(name="dyn-team")
        # Simulate callable members provider — set to a callable.
        team.members = lambda: [_FakeAgnoMember("dynamic")]
        adapter = AgnoAdapter(profile=_profile())
        adapter.instrument_team(team)

        topology = adapter._buffer.topology
        assert topology is not None
        assert len(topology.members) == 1  # just leader
        assert topology.leader().agent_id == "dyn-team"


@pytest.fixture
def stub_crewai_events(monkeypatch: pytest.MonkeyPatch):
    """Inject a fake crewai.events module with v0.8 event classes."""
    pkg = types.ModuleType("crewai")
    pkg.__path__ = []  # type: ignore[attr-defined]
    events = types.ModuleType("crewai.events")

    class _Sink:
        def __init__(self):
            self.handlers: dict[type, list[Any]] = {}

        def on(self, cls):
            def deco(fn):
                self.handlers.setdefault(cls, []).append(fn)
                return fn
            return deco

        def emit(self, source, event):
            for cls, fns in self.handlers.items():
                if isinstance(event, cls):
                    for fn in fns:
                        fn(source, event)

    class CrewKickoffStartedEvent: ...
    class CrewKickoffCompletedEvent: ...
    class TaskStartedEvent: ...
    class ToolUsageStartedEvent: ...
    class ToolUsageFinishedEvent: ...
    class ToolUsageErrorEvent: ...
    class AgentExecutionStartedEvent: ...
    class AgentExecutionCompletedEvent: ...

    bus = _Sink()
    for cls in (
        CrewKickoffStartedEvent,
        CrewKickoffCompletedEvent,
        TaskStartedEvent,
        ToolUsageStartedEvent,
        ToolUsageFinishedEvent,
        ToolUsageErrorEvent,
        AgentExecutionStartedEvent,
        AgentExecutionCompletedEvent,
    ):
        setattr(events, cls.__name__, cls)
    events.crewai_event_bus = bus

    monkeypatch.setitem(sys.modules, "crewai", pkg)
    monkeypatch.setitem(sys.modules, "crewai.events", events)
    return bus, events


class _FakeCrewAgent:
    def __init__(self, role):
        self.role = role


class _FakeCrew:
    def __init__(self, agents, process="sequential"):
        self.agents = agents
        self.process = process


class TestCrewAIAdapterTopology:
    def test_subscribe_with_crew_declares_topology(self, stub_crewai_events):
        adapter = CrewAIAdapter(profile=_profile())
        crew = _FakeCrew(
            agents=[
                _FakeCrewAgent("researcher"),
                _FakeCrewAgent("analyst"),
                _FakeCrewAgent("writer"),
            ],
            process="sequential",
        )
        adapter.subscribe(crew=crew)

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.HUB_SPOKE
        assert len(topology.members) == 3
        # First agent is treated as the LEADER for parent_id linkage.
        leader = topology.leader()
        assert leader is not None
        assert leader.agent_id == "researcher"

    def test_subscribe_with_hierarchical_crew(self, stub_crewai_events):
        adapter = CrewAIAdapter(profile=_profile())
        crew = _FakeCrew(
            agents=[_FakeCrewAgent("manager"), _FakeCrewAgent("worker")],
            process="hierarchical",
        )
        adapter.subscribe(crew=crew)

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.HIERARCHICAL_DAG

    def test_subscribe_global_no_topology(self, stub_crewai_events):
        """subscribe() with no crew (global) does not declare topology."""
        adapter = CrewAIAdapter(profile=_profile())
        adapter.subscribe()
        assert adapter._buffer.topology is None


class TestCrewAITaskSemanticFix:
    """v0.8 corrects the tasks-as-subagents bug. TaskStartedEvent now
    fires task_started, NOT subagent_start. Agent execution boundaries
    map to subagent_start/stop."""

    def test_task_started_event_fires_task_started_not_subagent(
        self, stub_crewai_events
    ):
        bus, events = stub_crewai_events
        adapter = CrewAIAdapter(profile=_profile())
        adapter.subscribe()

        task_event = events.TaskStartedEvent()
        task_event.task_id = "task-1"  # type: ignore[attr-defined]
        task_event.description = "summarize the report"  # type: ignore[attr-defined]
        task_event.agent = _FakeCrewAgent("summarizer")  # type: ignore[attr-defined]
        bus.emit(None, task_event)

        # task_started fires; subagent_start does NOT.
        task_events = [e for e in adapter.events if e.event_type == "task_started"]
        sub_events = [e for e in adapter.events if e.event_type == "subagent_start"]
        assert len(task_events) == 1
        assert task_events[0].data["task_id"] == "task-1"
        assert len(sub_events) == 0
        # Buffer: tasks gets entries, subagents stays empty.
        assert len(adapter._buffer.tasks) == 1
        assert len(adapter._buffer.subagents) == 0

    def test_agent_execution_fires_subagent_start_stop(self, stub_crewai_events):
        bus, events = stub_crewai_events
        adapter = CrewAIAdapter(profile=_profile())
        adapter.subscribe()

        start_event = events.AgentExecutionStartedEvent()
        start_event.agent = _FakeCrewAgent("researcher")  # type: ignore[attr-defined]
        bus.emit(None, start_event)

        end_event = events.AgentExecutionCompletedEvent()
        end_event.agent = _FakeCrewAgent("researcher")  # type: ignore[attr-defined]
        bus.emit(None, end_event)

        sub_starts = [e for e in adapter.events if e.event_type == "subagent_start"]
        sub_stops = [e for e in adapter.events if e.event_type == "subagent_stop"]
        assert len(sub_starts) == 1
        assert sub_starts[0].data["agent_id"] == "researcher"
        assert len(sub_stops) == 1

    def test_legacy_task_mapping_emits_deprecation_warning(self, stub_crewai_events):
        adapter = CrewAIAdapter(profile=_profile())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            adapter.subscribe(legacy_task_mapping=True)
        deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation) == 1
        assert "subagent_start" in str(deprecation[0].message)

    def test_legacy_task_mapping_restores_v07_behavior(self, stub_crewai_events):
        bus, events = stub_crewai_events
        adapter = CrewAIAdapter(profile=_profile())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            adapter.subscribe(legacy_task_mapping=True)

        task_event = events.TaskStartedEvent()
        task_event.task_id = "task-1"  # type: ignore[attr-defined]
        bus.emit(None, task_event)

        # v0.7 behavior: TaskStartedEvent → subagent_start
        sub_events = [e for e in adapter.events if e.event_type == "subagent_start"]
        assert len(sub_events) == 1
        assert sub_events[0].data["agent_id"] == "task-1"
        # task_started is NOT emitted in legacy mode.
        task_events = [e for e in adapter.events if e.event_type == "task_started"]
        assert task_events == []
