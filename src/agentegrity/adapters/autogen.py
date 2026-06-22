"""AutoGen (autogen-agentchat / autogen-core) adapter for agentegrity.

AutoGen has no callback-handler API; its only hook surface is
OpenTelemetry. Agent and tool execution emit GenAI semantic-convention
spans through ``trace.get_tracer("autogen-core")``, which means they
hit the **global** OTel tracer provider, not any provider passed to
``SingleThreadedAgentRuntime(tracer_provider=...)``. That arg only
catches runtime-internal message-routing spans, which agentegrity
does not care about.

The adapter therefore exposes a custom :class:`SpanProcessor` that
maps the three GenAI span types AutoGen emits onto canonical events:

    invoke_agent  start, root          ->  user_prompt_submit
    invoke_agent  start, non-root      ->  subagent_start
    execute_tool  start                ->  pre_tool_use
    execute_tool  end,   status OK     ->  post_tool_use
    execute_tool  end,   status ERROR  ->  post_tool_use_failure
    invoke_agent  end,   non-root      ->  subagent_stop
    invoke_agent  end,   root          ->  stop

The ``create_agent`` span is ignored: agent construction does not
emit a canonical event in our model.

Limitations:

* ``enforce=True`` is observation-only on this adapter. OTel spans are
  fire-and-observe; we cannot deny a tool call from a SpanProcessor.
  Passing ``enforce=True`` emits a warning and the block decision is
  still recorded in the attestation chain, but the tool runs anyway.
* ``execute_tool`` spans do not carry the tool's input arguments or
  return value as attributes (only ``gen_ai.tool.name`` and an
  optional ``gen_ai.tool.call.id``). Tool-use evaluation has access
  to the tool name but not the payload.

Usage::

    from agentegrity.autogen import instrument, report
    instrument()  # installs our SpanProcessor on the global TracerProvider
    await team.run(task="...")
    print(report())
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Any

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.profile import AgentProfile

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider

logger = logging.getLogger("agentegrity.adapters.autogen")

# GenAI semconv attribute keys. AutoGen ships its own copy of these
# constants (autogen_core._telemetry._genai) to avoid depending on
# opentelemetry-semantic-conventions. We hard-code the strings here for
# the same reason: the otel-semconv package is in incubation and pins
# tightly to a specific opentelemetry-api version, which would conflict
# with autogen-agentchat's own pin on a newer api.
_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
_GEN_AI_AGENT_NAME = "gen_ai.agent.name"
_GEN_AI_AGENT_ID = "gen_ai.agent.id"
_GEN_AI_TOOL_NAME = "gen_ai.tool.name"
_GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

_OP_INVOKE_AGENT = "invoke_agent"
_OP_EXECUTE_TOOL = "execute_tool"


class AutoGenAdapter(_BaseAdapter):
    """Instruments AutoGen agents and tools via an OpenTelemetry SpanProcessor."""

    _name = "autogen"

    def __init__(
        self,
        profile: AgentProfile,
        evaluator: IntegrityEvaluator | None = None,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> None:
        super().__init__(profile, evaluator, enforce, api_key)
        if enforce:
            warnings.warn(
                "AutoGenAdapter observes OpenTelemetry spans; enforce=True "
                "records block decisions in the attestation chain but cannot "
                "prevent tool calls (OTel hooks fire post-hoc). For "
                "enforcement, instrument at the agent layer or use a "
                "framework with a pre-tool hook.",
                UserWarning,
                stacklevel=2,
            )

    def span_processor(self) -> SpanProcessor:
        """Return an OTel SpanProcessor for use in an existing TracerProvider.

        Power-user API: if you already manage your own
        ``TracerProvider``, call ``add_span_processor(adapter.span_processor())``
        on it. For the zero-config path use ``instrument()`` instead.
        """
        try:
            from opentelemetry.sdk.trace import SpanProcessor as _SpanProcessor
        except ImportError:
            raise ImportError(
                "opentelemetry-sdk is required for the AutoGen adapter. "
                "Install it with: pip install agentegrity[autogen]"
            ) from None

        adapter = self

        # The lazy import above makes _SpanProcessor a real class at
        # runtime, but mypy can't see that when opentelemetry-sdk isn't
        # installed in the type-check environment (CI's [dev,crypto]
        # install). The misc-ignore handles that case; unused-ignore
        # silences the same comment when opentelemetry IS installed.
        class _AgentegritySpanProcessor(_SpanProcessor):  # type: ignore[misc, unused-ignore]
            def on_start(
                self, span: ReadableSpan, parent_context: Any = None
            ) -> None:
                adapter._on_span_start(span)

            def on_end(self, span: ReadableSpan) -> None:
                adapter._on_span_end(span)

            def shutdown(self) -> None:
                return None

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

        return _AgentegritySpanProcessor()

    def instrument(
        self, *, set_global: bool = True
    ) -> TracerProvider:
        """Build a TracerProvider wired to this adapter and (by default) install it globally.

        AutoGen's ``invoke_agent`` and ``execute_tool`` spans use the
        global OTel tracer (``trace.get_tracer("autogen-core")``), so
        the TracerProvider must be installed via
        ``opentelemetry.trace.set_tracer_provider`` for the adapter to
        receive events. Pass ``set_global=False`` if you intend to
        attach the returned provider yourself.
        """
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
        except ImportError:
            raise ImportError(
                "opentelemetry-sdk is required for the AutoGen adapter. "
                "Install it with: pip install agentegrity[autogen]"
            ) from None

        tracer_provider = _TracerProvider()
        tracer_provider.add_span_processor(self.span_processor())
        if set_global:
            existing = trace.get_tracer_provider()
            # OTel's default is _ProxyTracerProvider; replacing it is fine.
            # A user-set TracerProvider would be silently clobbered here,
            # so warn so they can wire the SpanProcessor onto theirs.
            if type(existing).__name__ not in ("_ProxyTracerProvider", "NoOpTracerProvider"):
                logger.warning(
                    "set_tracer_provider was already called with %s; replacing it. "
                    "To preserve your provider, call adapter.span_processor() and "
                    "add it to your existing TracerProvider instead.",
                    type(existing).__name__,
                )
            trace.set_tracer_provider(tracer_provider)
        return tracer_provider

    # --- Internal span -> event mapping ---

    def _on_span_start(self, span: ReadableSpan) -> None:
        attrs = span.attributes or {}
        op = attrs.get(_GEN_AI_OPERATION_NAME)
        if op == _OP_INVOKE_AGENT:
            agent_name = str(attrs.get(_GEN_AI_AGENT_NAME, ""))
            agent_id = str(attrs.get(_GEN_AI_AGENT_ID, "")) or agent_name
            if span.parent is None:
                # Root invoke_agent: declare a single-member
                # GROUP_CHAT topology. Subsequent nested
                # invoke_agent spans grow it via topology_change.
                self._seed_topology_for_root(agent_id, agent_name)
                self._dispatch("user_prompt_submit", {"prompt": agent_name})
            else:
                # Nested invoke_agent: ensure this agent is in the
                # topology before dispatching subagent_start so the
                # topology Evidence on the resulting attestation
                # already includes the new member.
                self._ensure_member(agent_id, agent_name)
                self._dispatch(
                    "subagent_start",
                    {"agent_id": agent_id},
                )
        elif op == _OP_EXECUTE_TOOL:
            self._dispatch(
                "pre_tool_use",
                {
                    "tool_name": str(attrs.get(_GEN_AI_TOOL_NAME, "")),
                    "tool_input": {
                        "tool_call_id": str(attrs.get(_GEN_AI_TOOL_CALL_ID, "")),
                    },
                },
            )

    def _on_span_end(self, span: ReadableSpan) -> None:
        attrs = span.attributes or {}
        op = attrs.get(_GEN_AI_OPERATION_NAME)
        if op == _OP_EXECUTE_TOOL:
            tool_name = str(attrs.get(_GEN_AI_TOOL_NAME, ""))
            status_ok = span.status.is_ok if span.status is not None else True
            if status_ok:
                self._dispatch(
                    "post_tool_use",
                    {"tool_name": tool_name, "tool_response": ""},
                )
            else:
                self._dispatch(
                    "post_tool_use_failure",
                    {
                        "tool_name": tool_name,
                        "error": str(attrs.get("error.type", "")),
                    },
                )
        elif op == _OP_INVOKE_AGENT:
            agent_name = str(attrs.get(_GEN_AI_AGENT_NAME, ""))
            if span.parent is None:
                self._dispatch("stop", {"agent": agent_name})
            else:
                self._dispatch(
                    "subagent_stop",
                    {"agent_id": str(attrs.get(_GEN_AI_AGENT_ID, "")) or agent_name},
                )

    # --- v0.8 multi-agent topology helpers ---

    def _seed_topology_for_root(self, agent_id: str, name: str) -> None:
        """Declare a single-member GROUP_CHAT topology at root span.

        AutoGen GroupChat has no fixed leader (agents are peers),
        so the root agent gets role PEER. Nested agents are appended
        by ``_ensure_member`` as their invoke_agent spans arrive.
        """
        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        existing = self._buffer.topology
        # If the topology is already seeded for this conversation,
        # don't reset it. AutoGen can re-start spans within the same
        # tracer-provider lifetime.
        if existing is not None and existing.leader() is None:
            for m in existing.members:
                if m.agent_id == agent_id:
                    return

        root = AgentMember(
            agent_id=agent_id,
            name=name or agent_id,
            role=AgentRole.PEER,
            capabilities=("tool_use",),
        )
        topology = AgentTopology(
            kind=TopologyKind.GROUP_CHAT,
            members=(root,),
            comm_channels=frozenset({"broadcast_channels"}),
        )
        self.set_topology(topology, my_role=AgentRole.PEER)

    def _ensure_member(self, agent_id: str, name: str) -> None:
        """Append an agent to the GROUP_CHAT topology if absent.

        AutoGen samples spans independently, so a nested
        invoke_agent may arrive before its root span fires. In that
        case we lazily seed a GROUP_CHAT with this agent as the
        first member; subsequent spans accumulate.
        """
        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        topology = self._buffer.topology
        if topology is None:
            # Root span dropped by sampling — start fresh here.
            self._seed_topology_for_root(agent_id, name)
            return
        if topology.member(agent_id) is not None:
            return
        new_topology = topology.with_member(AgentMember(
            agent_id=agent_id,
            name=name or agent_id,
            role=AgentRole.PEER,
            capabilities=("tool_use",),
        ))
        # Preserve GROUP_CHAT kind even if existing was something else.
        if new_topology.kind is not TopologyKind.GROUP_CHAT:
            new_topology = AgentTopology(
                kind=TopologyKind.GROUP_CHAT,
                members=new_topology.members,
                comm_channels=new_topology.comm_channels,
                topology_id=new_topology.topology_id,
                created_at=new_topology.created_at,
            )
        self.set_topology(new_topology, my_role=AgentRole.PEER)
