"""
CrewAI adapter for agentegrity.

Instruments CrewAI crews by subscribing to the global event bus
(``crewai.events.crewai_event_bus``) and forwarding each event to the
shared ``_BaseAdapter`` dispatcher. Tool calls are evaluated in a
before-tool-call hook instead, because the bus runs its handlers on a
thread pool and so cannot hold a tool back. Compatible with crewai 1.x; the
1.0 release relocated event classes from ``crewai.utilities.events``
to ``crewai.events`` (which re-exports the canonical ``types.*``
submodules).

Event mapping:

    CrewKickoffStartedEvent       -> user_prompt_submit
    before-tool-call hook         -> pre_tool_use
    ToolUsageFinishedEvent        -> post_tool_use
    ToolUsageErrorEvent           -> post_tool_use_failure
    LLMCallCompletedEvent         -> token usage (per call_id)
    TaskStartedEvent              -> task_started
    AgentExecutionStartedEvent    -> subagent_start
    AgentExecutionCompletedEvent  -> subagent_stop
    CrewKickoffCompletedEvent     -> stop

Tasks are not subagents in CrewAI's data model, so ``TaskStartedEvent``
maps to ``task_started`` and agent boundaries come from the
``AgentExecution*`` events.

Enforcement: CrewAI skips a tool when a before-tool-call hook returns
``False`` and gives the agent "Tool execution blocked by hook" as the
result. Under ``enforce=True`` the hook returns ``False`` for a denied
call, so the tool never runs and the crew continues.

``subscribe()`` replaces this adapter's earlier subscription, and
``close()`` removes the hook and every bus handler.

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

import logging
from typing import Any, Callable

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.usage import TokenUsage

logger = logging.getLogger("agentegrity.adapters.crewai")


class CrewAIAdapter(_BaseAdapter):
    """Instruments a CrewAI crew with agentegrity evaluation."""

    _name = "crewai"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tool_hook: Callable[[Any], bool | None] | None = None
        # (event class, handler) pairs on the bus, so a resubscribe or close can remove them.
        self._bus_handlers: list[tuple[Any, Callable[[Any, Any], None]]] = []

    def subscribe(self, crew: Any | None = None) -> None:
        """Subscribe to the CrewAI event bus, replacing any earlier subscription.

        If ``crew`` is None, subscribes globally (all crews in the
        process). Otherwise tool calls are evaluated only for the given
        crew and its copies (``kickoff_for_each``, ``train`` and ``test``
        run copies), and its topology is declared to this adapter.

        Parameters
        ----------
        crew : optional
            The CrewAI Crew instance. When provided, ``crew.agents``
            is read at subscribe time to construct an
            :class:`AgentTopology` and declare it via
            ``set_topology()``.
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
                crewai_event_bus,
            )
            from crewai.hooks import register_before_tool_call_hook
        except ImportError:
            raise ImportError(
                "crewai is required for the CrewAI adapter. "
                "Install it with: pip install agentegrity[crewai]"
            ) from None

        adapter = self

        def _on_kickoff_start(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "user_prompt_submit",
                {"prompt": getattr(event, "inputs", "") or ""},
            )

        def _on_kickoff_end(source_: Any, event: Any) -> None:
            adapter._dispatch("stop", {"output": str(getattr(event, "output", ""))})

        def _before_tool_call(context: Any) -> bool | None:
            if crew is not None and not _same_crew(getattr(context, "crew", None), crew):
                return None
            denied = adapter._tool_call_denial(
                getattr(context, "tool_name", ""), getattr(context, "tool_input", None)
            )
            return False if denied is not None else None

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

        def _on_task_start(source_: Any, event: Any) -> None:
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

        llm_call_event: Any
        try:
            from crewai.events import LLMCallCompletedEvent

            llm_call_event = LLMCallCompletedEvent
        except ImportError:
            # Older releases have no per-call LLM event; usage stays unreported.
            llm_call_event = None

        def _on_llm_call(source_: Any, event: Any) -> None:
            try:
                adapter._record_llm_call(event)
            except Exception as exc:
                logger.warning("usage capture failed: %s", exc)

        handlers: list[tuple[Any, Callable[[Any, Any], None]]] = [
            (CrewKickoffStartedEvent, _on_kickoff_start),
            (CrewKickoffCompletedEvent, _on_kickoff_end),
            (ToolUsageFinishedEvent, _on_tool_end),
            (ToolUsageErrorEvent, _on_tool_error),
            (TaskStartedEvent, _on_task_start),
            (AgentExecutionStartedEvent, _on_agent_start),
            (AgentExecutionCompletedEvent, _on_agent_end),
        ]
        if llm_call_event is not None:
            handlers.append((llm_call_event, _on_llm_call))

        self._unsubscribe()
        for event_class, handler in handlers:
            crewai_event_bus.on(event_class)(handler)
        self._bus_handlers = handlers
        register_before_tool_call_hook(_before_tool_call)
        self._tool_hook = _before_tool_call

        if crew is not None:
            self._declare_topology(crew)

    def close(self) -> None:
        """End the session and remove the hook and bus handlers."""
        self._unsubscribe()
        super().close()

    def _unsubscribe(self) -> None:
        """Remove what the last ``subscribe()`` registered."""
        if self._bus_handlers:
            from crewai.events import crewai_event_bus

            for event_class, handler in self._bus_handlers:
                crewai_event_bus.off(event_class, handler)
            self._bus_handlers = []
        if self._tool_hook is not None:
            from crewai.hooks import unregister_before_tool_call_hook

            unregister_before_tool_call_hook(self._tool_hook)
            self._tool_hook = None

    def _record_llm_call(self, event: Any) -> None:
        """Count one LLM call; CrewAI's normalizer makes the input count include the cache."""
        from crewai.types.usage_metrics import UsageMetrics

        # The normalizer first shipped in crewai 1.15; older releases report nothing.
        normalize = getattr(UsageMetrics, "from_provider_dict", None)
        if normalize is None:
            return
        metrics = normalize(getattr(event, "usage", None))
        if metrics is None:
            return
        call = getattr(event, "call_id", None) or getattr(event, "event_id", None)
        self.record_usage(f"crewai:{call}", getattr(event, "model", None), TokenUsage(
            input_tokens=metrics.prompt_tokens,
            output_tokens=metrics.completion_tokens,
            cache_read_tokens=metrics.cached_prompt_tokens,
            cache_write_tokens=metrics.cache_creation_tokens,
            reasoning_tokens=metrics.reasoning_tokens,
        ), source="provider_response")

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


def _same_crew(candidate: Any, crew: Any) -> bool:
    """Whether ``candidate`` is ``crew`` or a copy of it, which keeps its ``key`` but not its id."""
    if candidate is crew:
        return True
    key = getattr(crew, "key", None)
    return key is not None and getattr(candidate, "key", None) == key
