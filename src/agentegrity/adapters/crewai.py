"""
CrewAI adapter for agentegrity.

Instruments CrewAI crews by subscribing to the global event bus
(``crewai.events.crewai_event_bus``) and forwarding each event to the
shared ``_BaseAdapter`` dispatcher. Compatible with crewai 1.x; the
1.0 release relocated event classes from ``crewai.utilities.events``
to ``crewai.events`` (which re-exports the canonical ``types.*``
submodules).

Event mapping:

    CrewKickoffStartedEvent       -> user_prompt_submit
    ToolUsageStartedEvent         -> pre_tool_use
    ToolUsageFinishedEvent        -> post_tool_use
    ToolUsageErrorEvent           -> post_tool_use_failure
    TaskStartedEvent              -> task_started (v0.8+; was
                                     subagent_start pre-v0.8)
    AgentExecutionStartedEvent    -> subagent_start (v0.8+; new)
    AgentExecutionCompletedEvent  -> subagent_stop (v0.8+; new)
    CrewKickoffCompletedEvent     -> stop

v0.8 semantic fix: tasks are NOT subagents in CrewAI's data model.
Pre-v0.8 the adapter mapped ``TaskStartedEvent → subagent_start``
which counted tasks as subagents. v0.8 maps ``TaskStartedEvent`` to
the new ``task_started`` canonical event and adds the real agent
boundaries via ``AgentExecutionStartedEvent`` and
``AgentExecutionCompletedEvent``.

Backward compat: pass ``legacy_task_mapping=True`` to
``subscribe()`` to keep the v0.7 behavior (TaskStartedEvent →
subagent_start, no AgentExecution events). The legacy escape hatch
ships for one cycle (v0.8 only) and is removed in v0.9. Operators
hitting the deprecation warning should migrate their consumers
toward the corrected event shape.

Topology: when ``subscribe(crew=...)`` is called with a non-None
``crew``, the adapter constructs an :class:`AgentTopology` from
``crew.agents`` and declares it via ``set_topology()`` so the
four-layer pipeline sees the topology and the chain commits to it
via ``Evidence(evidence_type="topology")``. ``crew.process`` =
``sequential`` → ``HUB_SPOKE``; ``hierarchical`` →
``HIERARCHICAL_DAG``.

Usage::

    from agentegrity.crewai import instrument, report
    instrument(crew)          # subscribe + declare topology
    crew.kickoff()
    print(report())
"""

from __future__ import annotations

import warnings
from typing import Any

from agentegrity.adapters.base import _BaseAdapter


class CrewAIAdapter(_BaseAdapter):
    """Instruments a CrewAI crew with agentegrity evaluation."""

    _name = "crewai"

    def subscribe(
        self,
        crew: Any | None = None,
        *,
        legacy_task_mapping: bool = False,
    ) -> None:
        """Subscribe to the CrewAI event bus.

        If ``crew`` is None, subscribes globally (all crews in the
        process). Otherwise scopes the subscription to the given crew
        instance and declares the topology to this adapter.

        Parameters
        ----------
        crew : optional
            The CrewAI Crew instance. When provided, ``crew.agents``
            is read at subscribe time to construct an
            :class:`AgentTopology` and declare it via
            ``set_topology()``.
        legacy_task_mapping : bool
            v0.7 behavior shim. When True, ``TaskStartedEvent`` is
            mapped to ``subagent_start`` (the v0.7 semantic bug) and
            ``AgentExecution*`` events are NOT subscribed. Removed in
            v0.9. Emits a ``DeprecationWarning`` at subscribe time.
        """
        try:
            from crewai.events import (
                AgentExecutionCompletedEvent,
                AgentExecutionStartedEvent,
                CrewKickoffCompletedEvent,
                CrewKickoffStartedEvent,
                TaskStartedEvent,
                ToolUsageErrorEvent,
                ToolUsageFinishedEvent,
                ToolUsageStartedEvent,
                crewai_event_bus,
            )
        except ImportError:
            raise ImportError(
                "crewai is required for the CrewAI adapter. "
                "Install it with: pip install agentegrity[crewai]"
            ) from None

        adapter = self

        if legacy_task_mapping:
            warnings.warn(
                "legacy_task_mapping=True maps TaskStartedEvent to "
                "subagent_start (v0.7 behavior). This shim is removed "
                "in v0.9; migrate consumers to handle the corrected "
                "subagent_start (from AgentExecutionStartedEvent) and "
                "the new task_started event separately.",
                DeprecationWarning,
                stacklevel=2,
            )

        def _on_kickoff_start(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "user_prompt_submit",
                {"prompt": getattr(event, "inputs", "") or ""},
            )

        def _on_kickoff_end(source_: Any, event: Any) -> None:
            adapter._dispatch("stop", {"output": str(getattr(event, "output", ""))})

        def _on_tool_start(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "pre_tool_use",
                {
                    "tool_name": getattr(event, "tool_name", ""),
                    "tool_input": {"args": str(getattr(event, "tool_args", ""))},
                },
            )

        def _on_tool_end(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "post_tool_use",
                {
                    "tool_name": getattr(event, "tool_name", ""),
                    "tool_response": str(getattr(event, "output", "")),
                },
            )

        def _on_tool_error(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "post_tool_use_failure",
                {
                    "tool_name": getattr(event, "tool_name", ""),
                    "error": str(getattr(event, "error", "")),
                },
            )

        def _on_task_start_legacy(source_: Any, event: Any) -> None:
            # v0.7 behavior: TaskStartedEvent → subagent_start
            adapter._dispatch(
                "subagent_start",
                {"agent_id": getattr(event, "task_id", "") or str(id(event))},
            )

        def _on_task_start_v08(source_: Any, event: Any) -> None:
            # v0.8: tasks are tasks, not subagents.
            adapter._dispatch(
                "task_started",
                {
                    "task_id": getattr(event, "task_id", "") or str(id(event)),
                    "description": str(getattr(event, "description", "") or ""),
                    "agent_id": str(
                        getattr(getattr(event, "agent", None), "role", "")
                        or ""
                    ),
                },
            )

        def _on_agent_start(source_: Any, event: Any) -> None:
            agent = getattr(event, "agent", None) or source_
            agent_id = str(
                getattr(agent, "role", None)
                or getattr(agent, "id", None)
                or id(agent)
            )
            adapter._dispatch(
                "subagent_start",
                {"agent_id": agent_id},
            )

        def _on_agent_end(source_: Any, event: Any) -> None:
            agent = getattr(event, "agent", None) or source_
            agent_id = str(
                getattr(agent, "role", None)
                or getattr(agent, "id", None)
                or id(agent)
            )
            adapter._dispatch(
                "subagent_stop",
                {"agent_id": agent_id},
            )

        crewai_event_bus.on(CrewKickoffStartedEvent)(_on_kickoff_start)
        crewai_event_bus.on(CrewKickoffCompletedEvent)(_on_kickoff_end)
        crewai_event_bus.on(ToolUsageStartedEvent)(_on_tool_start)
        crewai_event_bus.on(ToolUsageFinishedEvent)(_on_tool_end)
        crewai_event_bus.on(ToolUsageErrorEvent)(_on_tool_error)

        if legacy_task_mapping:
            crewai_event_bus.on(TaskStartedEvent)(_on_task_start_legacy)
        else:
            crewai_event_bus.on(TaskStartedEvent)(_on_task_start_v08)
            crewai_event_bus.on(AgentExecutionStartedEvent)(_on_agent_start)
            crewai_event_bus.on(AgentExecutionCompletedEvent)(_on_agent_end)

        # v0.8: declare the topology when we know the crew.
        if crew is not None:
            self._declare_topology(crew)

    def _declare_topology(self, crew: Any) -> None:
        """Build and declare the AgentTopology from a CrewAI Crew."""
        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        agents = getattr(crew, "agents", None) or []
        process = str(getattr(crew, "process", "sequential") or "sequential").lower()
        # CrewAI Process is an enum; str(Process.sequential) is
        # 'Process.sequential'. Normalize.
        if "hierarch" in process:
            kind = TopologyKind.HIERARCHICAL_DAG
        else:
            kind = TopologyKind.HUB_SPOKE

        members: list[AgentMember] = []
        leader_id: str | None = None
        if kind == TopologyKind.HUB_SPOKE and agents:
            # In a sequential crew there isn't a single fixed "leader";
            # we treat the first declared agent as the topology root for
            # parent_id linkage. Honest framing: this is structural
            # convention, not crew semantics.
            first = agents[0]
            leader_id = str(
                getattr(first, "role", None)
                or getattr(first, "id", None)
                or id(first)
            )

        for i, agent in enumerate(agents):
            agent_id = str(
                getattr(agent, "role", None)
                or getattr(agent, "id", None)
                or id(agent)
            )
            if i == 0 and leader_id is not None:
                role = AgentRole.LEADER
                parent_id = None
            else:
                role = AgentRole.MEMBER if kind == TopologyKind.HUB_SPOKE else AgentRole.WORKER
                parent_id = leader_id
            members.append(AgentMember(
                agent_id=agent_id,
                name=agent_id,
                role=role,
                parent_id=parent_id,
                capabilities=("tool_use",),
            ))

        if not members:
            return

        topology = AgentTopology(
            kind=kind,
            members=tuple(members),
            comm_channels=frozenset({"peer_messages"}),
        )
        self.set_topology(topology, my_role=AgentRole.LEADER)
