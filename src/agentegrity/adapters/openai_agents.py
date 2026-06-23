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

Usage:
    from agents import Agent, Runner
    from agentegrity.openai_agents import run_hooks, report

    agent = Agent(name="my-agent", ...)
    await Runner.run(agent, input="...", hooks=run_hooks())
    print(report())
"""

from __future__ import annotations

import logging
from typing import Any

from agentegrity.adapters.base import _BaseAdapter

logger = logging.getLogger("agentegrity.adapters.openai_agents")


class OpenAIAgentsAdapter(_BaseAdapter):
    """Instruments an OpenAI Agents SDK run with agentegrity evaluation."""

    _name = "openai_agents"

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
