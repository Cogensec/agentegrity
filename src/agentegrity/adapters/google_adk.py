"""
Google Agent Development Kit (ADK) adapter for agentegrity.

Instruments ``google.adk`` agents by putting callbacks ahead of the
user's on the agent and on every agent reachable from it (``sub_agents``,
and agents wrapped in an ``AgentTool``):

    before_agent_callback  -> user_prompt_submit (instrumented agent)
                              subagent_start (any other agent)
    after_agent_callback   -> stop / subagent_stop
    before_tool_callback   -> pre_tool_use
    after_tool_callback    -> post_tool_use
    after_model_callback   -> token usage (final responses; partial chunks skipped)

ADK does not run an agent's after-agent callbacks once it has transferred
to another agent, so a run that ends in a transfer reports no ``stop``.

Enforcement: ADK skips a tool when a before-tool callback returns a
dict, and passes that dict to the model as the tool's result. Under
``enforce=True`` a denied call returns ``{"error": <denial code>}``, so the
tool never runs, the model sees the denial and the run continues.

Tool nodes in an ADK workflow graph run only plugin callbacks. Pass the
``Runner`` or the ``App`` to ``instrument()`` to also register an ADK plugin,
which gates every tool call in the app, workflow tool nodes included. Agent
callbacks then leave the calls the plugin has seen to it. Instrument an
``App`` before building its ``Runner``, which copies the app's plugins.

Usage:
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from agentegrity.google_adk import instrument, report

    runner = instrument(InMemoryRunner(agent=LlmAgent(name="my-agent", ...)))
    print(report())
"""

from __future__ import annotations

import inspect
import logging
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.usage import TokenUsage

logger = logging.getLogger("agentegrity.adapters.google_adk")
PLUGIN_NAME = "agentegrity"
# The tool call the plugin has just handled. ADK runs plugin callbacks before
# the agent's, in the same task, with the same ToolContext, so agent callbacks
# skip a call whose context matches instead of evaluating it twice.
_PLUGIN_HANDLED: ContextVar[Any] = ContextVar("agentegrity_adk_plugin_handled", default=None)
# The ToolContext of the call this task last denied. ADK still runs after-tool
# callbacks on the denial it handed the model; that is not a tool result.
_DENIED: ContextVar[Any] = ContextVar("agentegrity_adk_denied", default=None)


class GoogleADKAdapter(_BaseAdapter):
    """Instruments a Google ADK agent with agentegrity evaluation."""

    _name = "google_adk"
    _can_raise_on_deny = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._instrumented: list[Any] = []

    def instrument(self, target: Any) -> Any:
        """Attach agentegrity to a Google ADK agent, ``Runner`` or ``App``; returns ``target``.

        Callbacks go on the agent and on every agent reachable from it
        (``sub_agents``, and agents wrapped in an ``AgentTool``), ahead of
        any callbacks already set. ADK stops at the first callback that
        returns a value. These return nothing except when enforcement denies
        a tool call, so otherwise the user's callbacks still run and decide.
        Given a ``Runner`` or an ``App``, the plugin from :meth:`plugin` is
        registered as well, so workflow tool nodes are gated too.
        Instrumenting again is a no-op.
        """
        agent = self._register_plugin(target)
        try:
            for reached in _reachable(agent):
                # ADK agents are unhashable, so instrumented agents are matched by identity.
                if not any(reached is done for done in self._instrumented):
                    self._attach(reached, is_root=reached is agent)
                    self._instrumented.append(reached)
        except Exception as exc:
            raise ImportError(
                "google-adk is required for the Google ADK adapter, or the "
                "passed object is not a Google ADK Agent. "
                "Install it with: pip install agentegrity[google-adk]"
            ) from exc

        # v0.8: if the agent has sub_agents (SequentialAgent /
        # ParallelAgent / LoopAgent), declare a HIERARCHICAL_DAG
        # topology. Plain Agent without sub_agents stays single-agent.
        self._maybe_declare_workflow_topology(agent)
        return target

    def _register_plugin(self, target: Any) -> Any:
        """Register the plugin on a ``Runner`` or ``App``; return the agent to instrument."""
        try:
            from google.adk.apps import App
            from google.adk.runners import Runner
        except ImportError:
            return target
        if isinstance(target, Runner):
            if target.plugin_manager.get_plugin(PLUGIN_NAME) is None:
                target.plugin_manager.register_plugin(self.plugin())
            return target.agent
        if isinstance(target, App):
            if not any(p.name == PLUGIN_NAME for p in target.plugins):
                target.plugins.append(self.plugin())
            return target.root_agent
        return target

    def _attach(self, agent: Any, is_root: bool) -> None:
        """Put this adapter's callbacks first on one agent."""
        name = str(getattr(agent, "name", "") or "")
        adapter = self

        def _before_agent(callback_context: Any) -> None:
            if is_root:
                prompt = str(getattr(callback_context, "user_content", "") or "")
                adapter._dispatch("user_prompt_submit", {"prompt": prompt})
            else:
                adapter._dispatch("subagent_start", {"agent_id": name})

        def _after_agent(callback_context: Any) -> None:
            if is_root:
                adapter._dispatch("stop", {})
            else:
                adapter._dispatch("subagent_stop", {"agent_id": name})

        # Async, so a gate waiting on an LLM or SLM check leaves ADK's event loop free.
        async def _before_tool(tool: Any, args: Any, tool_context: Any) -> dict[str, str] | None:
            if adapter._plugin_handled(tool_context):
                return None
            return await adapter._gate_tool(tool, args, tool_context)

        def _after_tool(tool: Any, args: Any, tool_context: Any, tool_response: Any) -> None:
            if not adapter._plugin_handled(tool_context):
                adapter._record_tool_result(tool, tool_response, tool_context)

        def _after_model(callback_context: Any, llm_response: Any) -> None:
            adapter._record_model_response(agent, llm_response)

        # A workflow graph (``Workflow``) has no agent callbacks; the plugin covers its tools.
        if hasattr(agent, "before_agent_callback"):
            agent.before_agent_callback = _prepend(agent.before_agent_callback, _before_agent)
            agent.after_agent_callback = _prepend(
                getattr(agent, "after_agent_callback", None), _after_agent)
        # Workflow agents have no tool or model callbacks and reject unknown fields.
        if hasattr(agent, "before_tool_callback"):
            agent.before_tool_callback = _prepend(agent.before_tool_callback, _before_tool)
            agent.after_tool_callback = _prepend(
                getattr(agent, "after_tool_callback", None), _after_tool)
        if hasattr(agent, "after_model_callback"):
            agent.after_model_callback = _prepend(agent.after_model_callback, _after_model)

    async def _gate_tool(self,
        tool: Any,
        args: Any,
        tool_context: Any,
    ) -> dict[str, str] | None:
        """``{"error": <denial>}`` for a denied call, which ADK hands the model as the result."""
        reason = await self._atool_call_denial(getattr(tool, "name", str(tool)), args)
        if reason is None:
            return None
        _DENIED.set(tool_context)
        return {"error": reason}

    def _record_tool_result(self, tool: Any, response: Any, tool_context: Any) -> None:
        """Record a tool's result, unless it is the denial handed back for a call that never ran."""
        if _DENIED.get() is tool_context:
            return
        self._dispatch("post_tool_use", {
            "tool_name": getattr(tool, "name", str(tool)),
            "tool_response": str(response),
        })

    def _plugin_handled(self, tool_context: Any) -> bool:
        """Whether this adapter's plugin already handled the call with this context."""
        handled = _PLUGIN_HANDLED.get()
        return handled is not None and handled[0] is self and handled[1] is tool_context

    def plugin(self) -> Any:
        """Return an ADK plugin that gates every tool call in the app it is registered on."""
        try:
            from google.adk.plugins.base_plugin import BasePlugin
        except ImportError:
            raise ImportError(
                "google-adk is required for the Google ADK adapter. "
                "Install it with: pip install agentegrity[google-adk]"
            ) from None
        adapter = self

        class AgentegrityPlugin(BasePlugin):  # type: ignore[misc, unused-ignore]
            """Gates tool calls for every agent and workflow tool node in the app."""

            async def before_tool_callback(self, *, tool: Any, tool_args: dict[str, Any],
                                           tool_context: Any) -> dict[str, Any] | None:
                _PLUGIN_HANDLED.set((adapter, tool_context))
                return await adapter._gate_tool(tool, tool_args, tool_context)

            async def after_tool_callback(self, *, tool: Any, tool_args: dict[str, Any],
                                          tool_context: Any, result: Any) -> None:
                _PLUGIN_HANDLED.set((adapter, tool_context))
                adapter._record_tool_result(tool, result, tool_context)
                return None

        return AgentegrityPlugin(name=PLUGIN_NAME)

    def _record_model_response(self, agent: Any, response: Any) -> None:
        """Count one model call from its final response; streamed partials are skipped.

        ``prompt_token_count`` already includes cached tokens. Thinking and
        tool-use prompt tokens are reported beside the prompt and candidate
        counts, so they are added in.
        """
        usage = getattr(response, "usage_metadata", None)
        if usage is None or getattr(response, "partial", None) is True:
            return
        thoughts = _optional(usage, "thoughts_token_count")
        model = getattr(response, "model_version", None) or getattr(agent, "model", None)
        self.record_usage(f"google-adk:{uuid4().hex}", model if isinstance(model, str) else None,
                          TokenUsage(
            input_tokens=_count(usage, "prompt_token_count")
            + _count(usage, "tool_use_prompt_token_count"),
            output_tokens=_count(usage, "candidates_token_count") + (thoughts or 0),
            cache_read_tokens=_optional(usage, "cached_content_token_count"),
            cache_write_tokens=_optional(usage, "cache_creation_input_tokens"),
            reasoning_tokens=thoughts,
        ), source="provider_response")

    def _maybe_declare_workflow_topology(self, agent: Any) -> None:
        """Walk a Google ADK workflow agent's sub_agents and declare a
        HIERARCHICAL_DAG topology.

        ``SequentialAgent`` / ``ParallelAgent`` / ``LoopAgent`` all
        expose ``sub_agents``. A plain ``Agent`` does not — that case
        is correctly single-agent and we skip declaration.
        """
        sub_agents = getattr(agent, "sub_agents", None)
        if not sub_agents:
            return

        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        supervisor_id = str(getattr(agent, "name", None) or id(agent))
        members: list[AgentMember] = [AgentMember(
            agent_id=supervisor_id,
            name=supervisor_id,
            role=AgentRole.SUPERVISOR,
            capabilities=("tool_use",),
        )]
        for sub in sub_agents:
            sub_id = str(getattr(sub, "name", None) or id(sub))
            members.append(AgentMember(
                agent_id=sub_id,
                name=sub_id,
                role=AgentRole.WORKER,
                parent_id=supervisor_id,
                capabilities=("tool_use",),
            ))

        topology = AgentTopology(
            kind=TopologyKind.HIERARCHICAL_DAG,
            members=tuple(members),
            comm_channels=frozenset(),
        )
        self.set_topology(topology, my_role=AgentRole.SUPERVISOR)


def _optional(source: Any, name: str) -> int | None:
    value = getattr(source, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _count(source: Any, name: str) -> int:
    return _optional(source, name) or 0


def _safe(fn: Any) -> Any:
    """Run an agentegrity callback, sync or async, without ever failing the agent's run."""
    if inspect.iscoroutinefunction(fn):
        async def _async_callback(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                logger.warning("google_adk agentegrity callback failed: %s", exc)
                return None
        return _async_callback

    def _callback(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.warning("google_adk agentegrity callback failed: %s", exc)
            return None
    return _callback


def _prepend(existing: Any, fn: Any) -> Any:
    """Run ``fn`` ahead of the callback, or list of callbacks, already set.

    ADK takes the first value a callback returns, so a value from ``fn``
    (an enforcement denial) decides; otherwise the user's callbacks do.
    """
    ours = _safe(fn)
    if existing is None:
        return ours
    if isinstance(existing, (list, tuple)):
        return [ours, *existing]

    if inspect.iscoroutinefunction(ours):
        # ADK awaits a callback's result when it is awaitable, whichever callback returned it.
        async def _async_chained(*args: Any, **kwargs: Any) -> Any:
            result = await ours(*args, **kwargs)
            if result is not None:
                return result
            result = existing(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        return _async_chained

    def _chained(*args: Any, **kwargs: Any) -> Any:
        result = ours(*args, **kwargs)
        if result is not None:
            return result
        return existing(*args, **kwargs)

    return _chained


def _reachable(root: Any) -> list[Any]:
    """The agent, its sub-agents, and agents wrapped as tools, transitively."""
    found: list[Any] = []
    pending = [root]
    while pending:
        agent = pending.pop()
        if agent is None or any(agent is seen for seen in found):
            continue
        found.append(agent)
        pending.extend(getattr(agent, "sub_agents", None) or [])
        pending.extend(getattr(tool, "agent", None) for tool in getattr(agent, "tools", None) or [])
    return found
