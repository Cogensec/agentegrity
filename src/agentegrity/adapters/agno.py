"""Agno (>=2.0) adapter for agentegrity.

Agno exposes three hook surfaces on both ``Agent`` and ``Team``:

* ``pre_hooks``  — run before the agent/team processes input. Receive
  ``run_input`` (with ``.input_content``) plus ``agent``/``team``,
  ``session``, ``run_context`` (bound by signature inspection).
* ``post_hooks`` — run after the run completes. Receive ``run_output``
  (with ``.content`` / ``.get_content_as_string()``).
* ``tool_hooks`` — a middleware chain wrapping every tool call. Each
  hook has the shape ``hook(name, func, arguments)``; it calls
  ``func(**arguments)`` to continue the chain and observes the result.

Event mapping:

    pre_hook   (standalone / team leader)  ->  user_prompt_submit
    pre_hook   (team member)               ->  subagent_start
    tool_hook  entry                       ->  pre_tool_use
    tool_hook  success                     ->  post_tool_use
    tool_hook  exception                   ->  post_tool_use_failure
    post_hook  (team member)               ->  subagent_stop
    post_hook  (standalone / team leader)  ->  stop

Notes on Agno 2.x specifics:

* ``tool_hooks`` is re-propagated from ``agent.tool_hooks`` onto every
  tool at run setup, not at construction. Tools added after
  ``instrument()`` (e.g. ``agent.tools.append(...)``) are therefore
  still covered — no construction-time wrapping needed.
* Hooks are registered as **synchronous** callables. Agno runs sync
  hooks under both ``run()`` and ``arun()``; async hooks are skipped
  under sync ``run()``. Sync hooks are the portable choice.

Enforcement: under ``enforce=True`` the ``tool_hook`` evaluates the
``pre_tool_use`` event synchronously and, on a block decision, raises
``agno.exceptions.StopAgentRun`` before the tool runs. ``StopAgentRun``
is an ``AgentRunException`` subclass, the only exception family
``FunctionCall.execute()`` re-raises (via ``exception_to_raise``) to
halt the run; a plain ``Exception`` (or ``InputCheckError``, which
extends ``Exception`` directly) would be swallowed into a
``status="failure"`` result and the run would continue. Block
decisions are still recorded in the attestation chain.

Usage::

    from agentegrity.agno import instrument, report
    agent = instrument(agent)
    agent.run("...")
    print(report())

    # multi-agent
    team = instrument_team(team)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agentegrity.adapters.base import _BaseAdapter

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.team import Team

logger = logging.getLogger("agentegrity.adapters.agno")


class AgnoAdapter(_BaseAdapter):
    """Instruments Agno agents and teams with agentegrity evaluation."""

    _name = "agno"

    def instrument(self, agent: Agent) -> Agent:
        """Attach agentegrity hooks to a single Agno agent.

        Appends pre/post/tool hooks to the agent's existing hook lists
        (user-supplied hooks still fire). Returns the agent for chaining.
        """
        self._attach_hooks(agent, is_team_member=False)
        return agent

    def instrument_team(self, team: Team) -> Team:
        """Attach agentegrity hooks across a team and its members.

        The team leader emits the top-level ``user_prompt_submit`` /
        ``stop`` pair; each member emits ``subagent_start`` /
        ``subagent_stop``. Tool hooks are attached everywhere so tool
        calls are captured regardless of which member runs them. All
        members share this one adapter, so the attestation chain is
        unified across the whole team.

        v0.8: also declares an :class:`AgentTopology` of kind
        ``HUB_SPOKE`` (leader + members) to the adapter via
        ``set_topology()`` so the four-layer pipeline sees the
        topology and the chain commits to it via
        ``Evidence(evidence_type="topology")``.
        """
        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        self._attach_hooks(team, is_team_member=False)
        members = getattr(team, "members", None)

        leader_id = str(getattr(team, "name", "") or id(team))
        leader_member = AgentMember(
            agent_id=leader_id,
            name=leader_id,
            role=AgentRole.LEADER,
            capabilities=("tool_use",),
        )

        if callable(members):
            # Agno allows a callable that returns members at runtime; we
            # can't enumerate those statically. Tool hooks on the leader
            # still capture their tool calls; only subagent_* lifecycle
            # events are missed for dynamically-produced members. We
            # still declare a single-member topology so layers see at
            # least the leader; per-member additions are surfaced via
            # subagent_start events at runtime.
            logger.info(
                "agno team uses a callable members provider; subagent_* "
                "lifecycle events are only attached to statically-listed members."
            )
            topology = AgentTopology(
                kind=TopologyKind.HUB_SPOKE,
                members=(leader_member,),
                comm_channels=frozenset({"peer_messages"}),
            )
            self.set_topology(topology, my_role=AgentRole.LEADER)
            return team

        member_models: list[AgentMember] = [leader_member]
        for member in members or []:
            self._attach_hooks(member, is_team_member=True)
            member_id = str(getattr(member, "name", "") or id(member))
            member_models.append(AgentMember(
                agent_id=member_id,
                name=member_id,
                role=AgentRole.MEMBER,
                parent_id=leader_id,
                capabilities=("tool_use",),
            ))

        topology = AgentTopology(
            kind=TopologyKind.HUB_SPOKE,
            members=tuple(member_models),
            comm_channels=frozenset({"peer_messages"}),
        )
        self.set_topology(topology, my_role=AgentRole.LEADER)
        return team

    # --- Hook construction ---

    def _attach_hooks(self, target: Any, *, is_team_member: bool) -> None:
        adapter = self

        def _pre(run_input: Any) -> None:
            prompt = str(getattr(run_input, "input_content", "") or "")
            if is_team_member:
                adapter._dispatch(
                    "subagent_start",
                    {"agent_id": str(getattr(target, "name", "") or id(target))},
                )
            else:
                adapter._dispatch("user_prompt_submit", {"prompt": prompt})

        def _post(run_output: Any) -> None:
            if is_team_member:
                adapter._dispatch(
                    "subagent_stop",
                    {"agent_id": str(getattr(target, "name", "") or id(target))},
                )
            else:
                output = ""
                getter = getattr(run_output, "get_content_as_string", None)
                if callable(getter):
                    output = str(getter() or "")
                else:
                    output = str(getattr(run_output, "content", "") or "")
                adapter._dispatch("stop", {"output": output})

        def _tool(name: str, func: Any, arguments: dict[str, Any]) -> Any:
            decision = adapter._evaluate_sync(
                "pre_tool_use",
                {"tool_name": name, "tool_input": dict(arguments or {})},
            )
            hook_output = decision.get("hookSpecificOutput", {})
            if hook_output.get("permissionDecision") == "deny":
                # StopAgentRun (an AgentRunException subclass) is the only
                # exception FunctionCall.execute() re-raises to halt the run;
                # a plain Exception or InputCheckError would be swallowed into
                # a failure result. Imported lazily so the adapter stays
                # importable without agno installed.
                from agno.exceptions import StopAgentRun

                raise StopAgentRun(
                    hook_output.get("permissionDecisionReason")
                    or "agentegrity blocked this tool call"
                )
            try:
                result = func(**arguments)
            except Exception as exc:
                adapter._dispatch(
                    "post_tool_use_failure",
                    {"tool_name": name, "error": str(exc)},
                )
                raise
            adapter._dispatch(
                "post_tool_use",
                {"tool_name": name, "tool_response": str(result)},
            )
            return result

        _append_hook(target, "pre_hooks", _pre)
        _append_hook(target, "post_hooks", _post)
        _append_hook(target, "tool_hooks", _tool)


def _append_hook(target: Any, attr: str, hook: Any) -> None:
    """Append ``hook`` to ``target.<attr>``, creating the list if absent.

    Agno stores hooks as plain lists on the instance, so chaining onto
    any user-supplied hooks is a list append. A ``None`` (the default)
    becomes a fresh single-element list.
    """
    existing = getattr(target, attr, None)
    if existing is None:
        setattr(target, attr, [hook])
    else:
        existing.append(hook)
