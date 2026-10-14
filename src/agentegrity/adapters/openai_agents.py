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

Enforcement has two surfaces:

* ``instrument(agent)`` attaches a tool input guardrail to every function tool
  the agent can reach, including the tools of agents used through
  ``Agent.as_tool()`` and agents in ``handoffs`` (for ``handoff(agent)``
  objects, on SDK versions that keep a reference to the agent). A denied call returns the
  denial code to the model in place of the tool result (``reject_content``)
  and the run continues; with ``on_deny="raise"`` the run ends with
  :class:`IntegrityViolationError`.
* The run hooks gate any call whose tool carries no guardrail, such as MCP
  tools, which the SDK builds on every turn. Hooks cannot return a result in
  place of the tool, so a denial there raises and the run ends.

Limits: tools that run on OpenAI's side (web search, file search) and
handoffs cannot be blocked, and shell, local shell, computer and apply_patch
tools get no guardrails and receive no arguments in the hook, so they are
evaluated by name only.

Token usage comes from each model response, attributed to the agent's
model. An agent run as a tool shares its parent's ``Usage`` object but
fires no hooks of its own, so whatever the shared total holds beyond the
counted calls is recorded as one more entry with an unknown model.

Usage:
    from agents import Agent, Runner
    from agentegrity.openai_agents import instrument, run_hooks, report

    agent = instrument(Agent(name="my-agent", ...))
    await Runner.run(agent, input="...", hooks=run_hooks())
    print(report())
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass
from functools import cache
from typing import Any
from uuid import uuid4

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.monitor import IntegrityViolationError
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
        # The tool input guardrail instrument() attaches, built on first use.
        self._guardrail: Any = None

    def instrument(self, agent: Any) -> Any:
        """Gate every function tool ``agent`` can reach with a tool input guardrail.

        Walks the agent's tools, the agents behind ``Agent.as_tool()`` tools and
        the agents in ``handoffs``. A tool already carrying the guardrail is left
        as is, so instrumenting twice is safe. Returns the agent.
        """
        try:
            from agents import FunctionTool
        except ImportError:
            raise ImportError(
                "openai-agents 0.8 or later is required for instrument(). "
                "Install it with: pip install agentegrity[openai-agents]"
            ) from None
        if not _reaches_agent_tools():
            warnings.warn(
                "this openai-agents version does not expose the agent behind "
                "Agent.as_tool(), so tools inside agents used as tools are not gated",
                UserWarning,
                stacklevel=2,
            )
        guardrail = self._tool_guardrail()
        for tool in _function_tools(agent, FunctionTool):
            guardrails = list(tool.tool_input_guardrails or [])
            if not any(g is guardrail for g in guardrails):
                tool.tool_input_guardrails = [*guardrails, guardrail]
        return agent

    def _tool_guardrail(self) -> Any:
        """The ``ToolInputGuardrail`` that evaluates each call, shared by every tool."""
        if self._guardrail is not None:
            return self._guardrail
        from agents.tool_guardrails import ToolGuardrailFunctionOutput, ToolInputGuardrail

        adapter = self

        async def agentegrity_tool_gate(data: Any) -> Any:
            context = data.context
            reason = await adapter._atool_call_denial(
                getattr(context, "tool_name", ""), _tool_arguments(context)
            )
            if reason is None:
                return ToolGuardrailFunctionOutput.allow()
            if adapter._on_deny == "raise":
                raise _denial_error()(reason)
            return ToolGuardrailFunctionOutput.reject_content(reason)

        self._guardrail = ToolInputGuardrail(guardrail_function=agentegrity_tool_gate,
                                             name="agentegrity")
        return self._guardrail

    def _guards(self, tool: Any) -> bool:
        """Whether ``tool`` carries this adapter's guardrail, which already gates its calls."""
        return self._guardrail is not None and any(
            g is self._guardrail for g in getattr(tool, "tool_input_guardrails", None) or []
        )

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
                if adapter._guards(tool):
                    return
                tool_name = getattr(tool, "name", str(tool))
                reason = await adapter._atool_call_denial(tool_name, _tool_arguments(context))
                if reason is not None:
                    raise _denial_error()(reason)

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


@cache
def _denial_error() -> type[IntegrityViolationError]:
    """An :class:`IntegrityViolationError` the SDK re-raises as is.

    The SDK wraps exceptions from tool execution in ``UserError`` unless
    they derive from ``AgentsException``.
    """
    from agents.exceptions import AgentsException

    class ToolCallDenied(IntegrityViolationError, AgentsException):  # type: ignore[misc, unused-ignore]
        """Agentegrity denied a tool call under enforcement."""

    return ToolCallDenied


def _reaches_agent_tools() -> bool:
    """Whether the SDK keeps the agent behind ``Agent.as_tool()`` on the tool (0.8+)."""
    from agents import FunctionTool

    return "_agent_instance" in getattr(FunctionTool, "__dataclass_fields__", {})


def _function_tools(agent: Any, function_tool: type) -> list[Any]:
    """Every function tool reachable from ``agent``: its own, and those of nested agents."""
    tools: list[Any] = []
    seen: list[Any] = []
    pending = [agent]
    while pending:
        current = pending.pop()
        # Agents are unhashable dataclasses, so visited ones are matched by identity.
        if current is None or any(current is done for done in seen):
            continue
        seen.append(current)
        for tool in getattr(current, "tools", None) or []:
            if isinstance(tool, function_tool):
                tools.append(tool)
                # The agent behind Agent.as_tool() runs its tools in a nested run.
                pending.append(getattr(tool, "_agent_instance", None))
        pending.extend(_handoff_agent(h) for h in getattr(current, "handoffs", None) or [])
    return tools


def _handoff_agent(entry: Any) -> Any:
    """The agent a ``handoffs`` entry leads to: the entry itself, or a ``Handoff``'s target.

    A ``Handoff`` from ``handoff(agent)`` keeps its agent only in the private
    ``_agent_ref`` weakref, which recent SDK versions set; older ones keep none.
    """
    if hasattr(entry, "tools"):
        return entry
    ref = getattr(entry, "_agent_ref", None)
    return ref() if callable(ref) else None


def _tool_arguments(context: Any) -> dict[str, Any]:
    """The call's arguments, which the SDK passes to hooks as a JSON string on ``ToolContext``."""
    raw = getattr(context, "tool_arguments", None)
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"arguments": raw}
    return parsed if isinstance(parsed, dict) else {"arguments": parsed}


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
