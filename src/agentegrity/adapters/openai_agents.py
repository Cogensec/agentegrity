"""
OpenAI Agents SDK adapter for agentegrity.

Instruments agents built on the OpenAI Agents SDK (``agents`` package)
by subclassing ``RunHooks`` and forwarding to the shared ``_BaseAdapter``
event dispatcher.

Event mapping:
    on_agent_start     -> user_prompt_submit
    on_tool_start      -> pre_tool_use
    on_tool_end        -> post_tool_use
    on_handoff         -> subagent_start
    on_agent_end       -> stop
    on_llm_end         -> token usage

Token usage comes from each model response, attributed to the agent's
model. An agent run as a tool shares its parent's ``Usage`` object but
fires no hooks of its own, so whatever the shared total holds beyond the
counted calls is recorded as one more entry with an unknown model.

Usage:
    from agents import Agent, Runner
    from agentegrity.openai_agents import run_hooks, report

    agent = Agent(name="my-agent", ...)
    await Runner.run(agent, input="...", hooks=run_hooks())
    print(report())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.usage import UNKNOWN_MODEL, TokenUsage

logger = logging.getLogger("agentegrity.adapters.openai_agents")


class OpenAIAgentsAdapter(_BaseAdapter):
    """Instruments an OpenAI Agents SDK run with agentegrity evaluation."""

    _name = "openai_agents"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # One entry per run's shared Usage object. The object is held so its
        # id cannot be reused by a later run while this one is tracked.
        self._runs: dict[int, _Run] = {}

    def _record_response(self, context: Any, agent: Any, response: Any) -> None:
        """Count one model call, then any calls the shared total holds beyond it."""
        shared = getattr(context, "usage", None)
        run = self._runs.get(id(shared))
        if run is None or run.shared is not shared:
            run = self._runs[id(shared)] = _Run(shared, uuid4().hex)
        call = _sdk_usage(getattr(response, "usage", None))
        if call is not None:
            run.calls += 1
            call_id = getattr(response, "response_id", None) or f"call-{run.calls}"
            self.record_usage(f"openai-agents:{run.key}:{call_id}", _agent_model(agent), call,
                              source=SOURCE)
            run.counted = run.counted + call
        total = _sdk_usage(shared)
        nested = _remainder(total, run.counted) if total is not None else None
        if nested is not None:
            self.record_usage(f"openai-agents:{run.key}:nested", UNKNOWN_MODEL, nested,
                              source=SOURCE)

    def create_run_hooks(self) -> Any:
        """Return a ``RunHooks`` subclass instance bound to this adapter.

        Imports ``RunHooks`` at call time so the adapter module can be
        imported without the ``openai-agents`` package installed.
        """
        try:
            from agents import RunHooks
        except ImportError:
            raise ImportError(
                "openai-agents is required for the OpenAI Agents adapter. "
                "Install it with: pip install agentegrity[openai-agents]"
            ) from None

        adapter = self

        class _AgentegrityRunHooks(RunHooks):  # type: ignore[misc, unused-ignore]
            async def on_agent_start(
                self, context: Any, agent: Any
            ) -> None:
                prompt = ""
                try:
                    prompt = str(getattr(context, "input", "") or "")
                except Exception:
                    pass
                # v0.8: seed a PEER_TO_PEER topology with the starting
                # agent. Handoffs grow the topology incrementally.
                agent_id = str(getattr(agent, "name", "") or id(agent))
                adapter._seed_topology_from_initial(agent_id)
                await adapter.on_event("user_prompt_submit", {"prompt": prompt})

            async def on_agent_end(
                self, context: Any, agent: Any, output: Any
            ) -> None:
                await adapter.on_event("stop", {"output": str(output)})

            async def on_llm_end(
                self, context: Any, agent: Any, response: Any
            ) -> None:
                try:
                    adapter._record_response(context, agent, response)
                except Exception as exc:
                    logger.warning("usage capture failed: %s", exc)

            async def on_tool_start(
                self, context: Any, agent: Any, tool: Any
            ) -> None:
                tool_name = getattr(tool, "name", str(tool))
                await adapter.on_event(
                    "pre_tool_use",
                    {"tool_name": tool_name, "tool_input": {}},
                )

            async def on_tool_end(
                self, context: Any, agent: Any, tool: Any, result: Any
            ) -> None:
                tool_name = getattr(tool, "name", str(tool))
                await adapter.on_event(
                    "post_tool_use",
                    {"tool_name": tool_name, "tool_response": str(result)},
                )

            async def on_handoff(
                self, context: Any, from_agent: Any, to_agent: Any
            ) -> None:
                from_id = str(getattr(from_agent, "name", "") or id(from_agent))
                to_id = str(getattr(to_agent, "name", "") or id(to_agent))
                # v0.8: append the handoff target as a peer, growing the
                # PEER_TO_PEER topology.
                adapter._add_handoff_target(to_id)
                await adapter.on_event(
                    "subagent_start",
                    {"agent_id": to_id, "handoff_from": from_id},
                )

        return _AgentegrityRunHooks()

    def _seed_topology_from_initial(self, agent_id: str) -> None:
        """Declare a single-member PEER_TO_PEER topology at run start."""
        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        existing = self._buffer.topology
        if existing is not None:
            if existing.member(agent_id) is not None:
                return  # already there

        member = AgentMember(
            agent_id=agent_id,
            name=agent_id,
            role=AgentRole.PEER,
            capabilities=("tool_use",),
        )
        topology = AgentTopology(
            kind=TopologyKind.PEER_TO_PEER,
            members=(member,),
            comm_channels=frozenset({"peer_messages"}),
        )
        self.set_topology(topology, my_role=AgentRole.PEER)

    def _add_handoff_target(self, agent_id: str) -> None:
        """Append a handoff target to the PEER_TO_PEER topology."""
        from agentegrity.core.topology import AgentMember, AgentRole

        topology = self._buffer.topology
        if topology is None:
            self._seed_topology_from_initial(agent_id)
            return
        if topology.member(agent_id) is not None:
            return
        new_topology = topology.with_member(AgentMember(
            agent_id=agent_id,
            name=agent_id,
            role=AgentRole.PEER,
            capabilities=("tool_use",),
        ))
        self.set_topology(new_topology, my_role=AgentRole.PEER)


SOURCE = "provider_response"
_NOTHING = TokenUsage(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                      cache_write_tokens=0, reasoning_tokens=0, requests=0)


@dataclass
class _Run:
    shared: Any
    key: str
    counted: TokenUsage = _NOTHING
    calls: int = 0


def _sdk_usage(usage: Any) -> TokenUsage | None:
    """Normalize an Agents SDK ``Usage``; its input count already includes the cache."""
    if usage is None:
        return None
    inputs = getattr(usage, "input_tokens_details", None)
    outputs = getattr(usage, "output_tokens_details", None)
    return TokenUsage(
        input_tokens=_count(usage, "input_tokens"),
        output_tokens=_count(usage, "output_tokens"),
        cache_read_tokens=_count(inputs, "cached_tokens"),
        cache_write_tokens=_count(inputs, "cache_write_tokens"),
        reasoning_tokens=_count(outputs, "reasoning_tokens"),
        requests=_count(usage, "requests"),
    )


def _remainder(total: TokenUsage, counted: TokenUsage) -> TokenUsage | None:
    """What a running total holds beyond the calls already counted, if anything."""
    def left(name: str) -> int:
        return max(0, (getattr(total, name) or 0) - (getattr(counted, name) or 0))

    rest = TokenUsage(
        input_tokens=left("input_tokens"), output_tokens=left("output_tokens"),
        cache_read_tokens=left("cache_read_tokens"),
        cache_write_tokens=left("cache_write_tokens"),
        reasoning_tokens=left("reasoning_tokens"), requests=left("requests"),
    )
    return rest if rest.input_tokens or rest.output_tokens else None


def _agent_model(agent: Any) -> str | None:
    """The agent's model name: a string, or a model object's ``model``; None when unset."""
    model = getattr(agent, "model", None)
    if isinstance(model, str):
        return model or None
    name = getattr(model, "model", None)
    return name if isinstance(name, str) and name else None


def _count(source: Any, name: str) -> int:
    value = getattr(source, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
