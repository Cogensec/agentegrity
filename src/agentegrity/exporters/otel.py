"""OpenTelemetry exporter for agentegrity session data.

Translates the :class:`~agentegrity.adapters.base.SessionExporter`
callback stream (``on_session_start`` / ``on_event`` / ``on_session_end``)
into OpenTelemetry **traces** and **metrics** so any OTLP-compatible
backend (Grafana Tempo/Mimir, Honeycomb, Datadog, ...) can render
per-agent integrity posture without touching the agent.

Design
------
* **Traces.** Each session is one trace. ``on_session_start`` opens a
  long-lived root span (``agentegrity.session``); ``on_event`` opens a
  short child span per evaluated event; ``on_session_end`` closes the
  root.
* **Metrics.** Counters (evaluations, enforcement denials, health
  events) and histograms (composite score, per-layer score, evaluation
  latency) plus an active-session up/down counter. Metric attributes are
  kept low-cardinality (adapter, action, multi-agent flag); per-agent
  breakdown comes from the OTel ``service.name`` resource attribute the
  deploying process sets (e.g. ``OTEL_SERVICE_NAME``), which is the
  standard way to group telemetry by agent.
* **Logs.** Enforcement decisions and instrumentation-health events are
  recorded as **span events** plus a metric counter (the GenAI-convention
  pattern). A dedicated OTLP logs signal is deferred until the
  OpenTelemetry Python logs SDK leaves experimental status.
* **Semantic conventions.** Integrity data lives under the stable
  ``agentegrity.*`` namespace this project owns. GenAI semantic-convention
  attributes (``gen_ai.*``) are emitted opportunistically for interop
  where an event maps cleanly; they are never load-bearing because the
  GenAI conventions are still in development.
* **Privacy.** Raw prompt / tool content is never written to span
  attributes (a named GenAI anti-pattern: attributes are indexed,
  size-limited, and leak PII). Only safe fields and content hashes are
  emitted. Pass ``capture_content=True`` to additionally attach truncated
  content as a span event (droppable at the collector).

Chain integrity (v0.8.1 trust model)
------------------------------------
Two *different* signals, deliberately not conflated:

* ``agentegrity.chain.hash_linked`` mirrors the session summary's
  ``chain_hash_linked``. It proves only that records are hash-linked.
  ``content_hash`` is an unkeyed SHA-256, so anyone who controls the
  serialized chain can recompute the links. Linkage is **not**
  tamper-evidence, so a false value here does not mark the span as an
  error on its own.
* ``agentegrity.chain.signatures_verified`` is the real tamper-evidence:
  every record signed AND verifying, against a pinned ``trusted_keys``
  anchor. Emitted only when a ``chain_provider`` is supplied; the
  attribute is *absent* when signatures were never checked, so absent /
  true / false stay distinguishable. A false value does set span status
  to ERROR.

Single-member topology
----------------------
Topologies are dimensioned by ``agentegrity.topology.member_count`` and
flagged ``agentegrity.topology.multi_agent`` only at >= 2 members, so a
degenerate one-member topology does not inflate multi-agent counts in the
fleet view. (v0.9 tightens this at the adapter source.)

Fail-open: the registering adapter already catches and logs exporter
exceptions, but every public method here also guards its own body so a
telemetry error can never surface to the instrumented agent.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:
    from opentelemetry import metrics, trace
    from opentelemetry.trace import SpanKind, Status, StatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via the install hint
    _OTEL_AVAILABLE = False

logger = logging.getLogger("agentegrity.exporters.otel")

_INSTRUMENTATION_NAME = "agentegrity"

# Instrumentation-health events: not agent behavior, but gaps in the
# instrumentation itself. A buffer overflow means telemetry is being
# dropped, so the dashboard must be able to see it. Overflow channels are
# dynamic (``<channel>_overflow``), hence the suffix match.
_HEALTH_EVENTS = frozenset({"capture_failure", "subagent_orphan"})
_HEALTH_SUFFIX = "_overflow"

# Enforcement outcomes that deny an action under enforce=True. Since
# v0.8.1 ``escalate`` also fails closed (denies unless an approval
# handler approves), so it is counted alongside ``block``.
_DENYING_ACTIONS = frozenset({"block", "escalate"})

# Event-data keys that may carry raw, possibly-sensitive content. Hashed
# into attributes; only echoed verbatim (truncated) as a span event when
# capture_content is enabled.
_CONTENT_KEYS = ("prompt", "tool_input", "tool_response", "output", "content")

_MAX_CONTENT_CHARS = 512


def _is_health_event(event_type: str) -> bool:
    return event_type in _HEALTH_EVENTS or event_type.endswith(_HEALTH_SUFFIX)


@dataclass
class _SessionState:
    """Per-session trace + topology state held between callbacks."""

    root_span: Any
    context: Any
    adapter: str
    agent_id: str
    topology_member_count: int = 0
    topology_kind: str | None = None

    @property
    def multi_agent(self) -> bool:
        # A one-member topology is structurally single-agent (see module
        # docstring); only >= 2 members counts as multi-agent.
        return self.topology_member_count >= 2


def _hash(value: Any) -> str:
    """Stable SHA-256 hex of a value's string form (truncated to 16 chars)."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


class OTelSessionExporter:
    """A :class:`SessionExporter` that emits OpenTelemetry traces + metrics.

    Register it on any adapter::

        from agentegrity.langchain import register_exporter, instrument_graph
        from agentegrity.exporters.otel import OTelSessionExporter

        register_exporter(OTelSessionExporter())
        graph = instrument_graph(my_graph)

    By default it emits through the **global** OTel providers, so the
    deploying process configures the resource (``OTEL_SERVICE_NAME``),
    OTLP endpoint (``OTEL_EXPORTER_OTLP_ENDPOINT``), and exporters the
    standard way. Pass explicit ``tracer_provider`` / ``meter_provider``
    to override (used in tests and advanced setups).

    To also report cryptographic chain verification, pass a
    ``chain_provider`` returning the adapter's ``AttestationChain`` plus
    the pinned ``trusted_keys`` anchor::

        from agentegrity.langchain import adapter, register_exporter

        register_exporter(OTelSessionExporter(
            chain_provider=lambda: adapter().attestation_chain,
            trusted_keys={pinned_public_key_bytes},
        ))

    Without ``trusted_keys`` a forged chain signed with an
    attacker-generated key self-verifies, so pin a key set anywhere the
    chain crosses a trust boundary.
    """

    def __init__(self,
        *,
        tracer_provider: Any | None = None,
        meter_provider: Any | None = None,
        capture_content: bool = False,
        chain_provider: Callable[[], Any] | None = None,
        trusted_keys: set[bytes] | None = None,
    ) -> None:
        """Build the exporter. Raises ImportError if the [otel] extra is absent."""
        if not _OTEL_AVAILABLE:
            raise ImportError(
                "OpenTelemetry is required for OTelSessionExporter. "
                "Install it with: pip install agentegrity[otel]"
            )
        tp = tracer_provider or trace.get_tracer_provider()
        mp = meter_provider or metrics.get_meter_provider()
        self._tracer = tp.get_tracer(_INSTRUMENTATION_NAME)
        meter = mp.get_meter(_INSTRUMENTATION_NAME)
        self._capture_content = capture_content
        self._chain_provider = chain_provider
        self._trusted_keys = trusted_keys
        self._sessions: dict[str, _SessionState] = {}

        self._m_evals = meter.create_counter(
            "agentegrity.evaluations",
            unit="{evaluation}",
            description="Integrity evaluations, by adapter and action.",
        )
        self._m_denials = meter.create_counter(
            "agentegrity.enforcement.denials",
            unit="{denial}",
            description="Enforcement denials (block, or escalate without approval).",
        )
        self._m_health = meter.create_counter(
            "agentegrity.health_events",
            unit="{event}",
            description="Instrumentation-health events "
            "(capture_failure, subagent_orphan, buffer overflows).",
        )
        self._h_composite = meter.create_histogram(
            "agentegrity.score.composite",
            unit="1",
            description="Composite integrity score distribution.",
        )
        self._h_layer = meter.create_histogram(
            "agentegrity.score.layer",
            unit="1",
            description="Per-layer integrity score distribution.",
        )
        self._h_latency = meter.create_histogram(
            "agentegrity.evaluation.latency",
            unit="ms",
            description="Per-evaluation latency.",
        )
        self._g_active = meter.create_up_down_counter(
            "agentegrity.sessions.active",
            unit="{session}",
            description="Active instrumented sessions.",
        )

    def describe(self) -> dict[str, str]:
        """Identify this sink for ``get_summary()["exporters"]``.

        No ``target``: the OTLP destination is owned by the OpenTelemetry SDK's
        own configuration (``OTEL_EXPORTER_OTLP_ENDPOINT`` and the process's
        provider), not by this class, so reporting one here could contradict
        where spans actually go.
        """
        return {"type": type(self).__name__}

    # --- SessionExporter protocol ---

    async def on_session_start(self,
        session_id: str,
        adapter_name: str,
        profile: dict[str, Any],
    ) -> None:
        """Open the session root span and mark the session active."""
        try:
            agent_id = str(profile.get("agent_id", ""))
            root = self._tracer.start_span(
                "agentegrity.session",
                kind=SpanKind.INTERNAL,
                attributes={
                    "agentegrity.session.id": session_id,
                    "agentegrity.adapter": adapter_name,
                    "agentegrity.agent.id": agent_id,
                    "agentegrity.agent.name": str(profile.get("name", "")),
                    "agentegrity.agent.risk_tier": str(profile.get("risk_tier", "")),
                    # GenAI interop (best-effort; not load-bearing).
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": str(profile.get("name", "")),
                },
            )
            self._sessions[session_id] = _SessionState(
                root_span=root,
                context=trace.set_span_in_context(root),
                adapter=adapter_name,
                agent_id=agent_id,
            )
            self._g_active.add(1, {"agentegrity.adapter": adapter_name})
        except Exception as exc:  # fail-open
            logger.warning("otel on_session_start failed: %s", exc)

    async def on_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Emit one child span + metrics for an evaluated event."""
        try:
            state = self._sessions.get(session_id)
            if state is None:
                # session_start was missed (exporter registered late, or
                # it failed). Open a parentless root so the event isn't lost.
                state = self._lazy_session(session_id, event)

            event_type = str(event.get("event_type", ""))
            data = event.get("data") or {}
            self._update_topology(state, data)

            span = self._tracer.start_span(
                f"agentegrity.{event_type}",
                context=state.context,
                kind=SpanKind.INTERNAL,
                attributes=self._event_attributes(state, event_type, event, data),
            )
            try:
                self._record_metrics(state, event)
                self._record_enforcement_and_health(span, state, event_type, event)
                self._maybe_capture_content(span, data)
            finally:
                span.end()
        except Exception as exc:  # fail-open
            logger.warning("otel on_event failed: %s", exc)

    async def on_session_end(self, session_id: str, summary: dict[str, Any]) -> None:
        """Stamp the session summary onto the root span and close it."""
        try:
            state = self._sessions.pop(session_id, None)
            if state is None:
                return
            root = state.root_span
            # Hash linkage only — NOT tamper-evidence, so it never sets an
            # error status by itself (see module docstring).
            root.set_attribute(
                "agentegrity.chain.hash_linked",
                bool(summary.get("chain_hash_linked", False)),
            )
            root.set_attribute("agentegrity.evaluations", int(summary.get("evaluations", 0)))
            root.set_attribute(
                "agentegrity.attestation_records",
                int(summary.get("attestation_records", 0)),
            )
            root.set_attribute(
                "agentegrity.decision_records", int(summary.get("decision_records", 0))
            )
            root.set_attribute(
                "agentegrity.enforce_mode", bool(summary.get("enforce_mode", False))
            )
            self._record_signature_verification(root)
            root.end()
            self._g_active.add(-1, {"agentegrity.adapter": state.adapter})
        except Exception as exc:  # fail-open
            logger.warning("otel on_session_end failed: %s", exc)

    # --- internals ---

    def _record_signature_verification(self, root: Any) -> None:
        """Verify chain signatures and stamp the real tamper-evidence signal.

        Omits the attribute entirely when no ``chain_provider`` was
        configured, so "not checked" stays distinguishable from "failed".
        """
        if self._chain_provider is None:
            return
        try:
            chain = self._chain_provider()
            verified, bad_index = chain.verify_signatures(
                trusted_keys=self._trusted_keys
            )
        except Exception as exc:  # fail-open
            logger.warning("otel signature verification failed: %s", exc)
            return
        root.set_attribute("agentegrity.chain.signatures_verified", bool(verified))
        root.set_attribute("agentegrity.chain.trust_anchored", self._trusted_keys is not None)
        if not verified:
            if bad_index is not None:
                root.set_attribute("agentegrity.chain.first_bad_record", int(bad_index))
            root.set_status(Status(StatusCode.ERROR, "chain signature verification failed"))

    def _lazy_session(self, session_id: str, event: dict[str, Any]) -> _SessionState:
        adapter = str(event.get("adapter_name", ""))
        root = self._tracer.start_span(
            "agentegrity.session",
            kind=SpanKind.INTERNAL,
            attributes={
                "agentegrity.session.id": session_id,
                "agentegrity.adapter": adapter,
                "agentegrity.session.lazy": True,
            },
        )
        state = _SessionState(
            root_span=root,
            context=trace.set_span_in_context(root),
            adapter=adapter,
            agent_id="",
        )
        self._sessions[session_id] = state
        self._g_active.add(1, {"agentegrity.adapter": adapter})
        return state

    @staticmethod
    def _update_topology(state: _SessionState, data: dict[str, Any]) -> None:
        topo = data.get("topology")
        if isinstance(topo, dict):
            members = topo.get("members") or []
            state.topology_member_count = len(members)
            state.topology_kind = topo.get("kind")

    def _event_attributes(self,
        state: _SessionState,
        event_type: str,
        event: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "agentegrity.event.type": event_type,
            "agentegrity.adapter": state.adapter,
            "agentegrity.agent.id": state.agent_id,
        }
        if state.topology_kind is not None:
            attrs["agentegrity.topology.kind"] = state.topology_kind
            attrs["agentegrity.topology.member_count"] = state.topology_member_count
            attrs["agentegrity.topology.multi_agent"] = state.multi_agent

        result = event.get("evaluation_result")
        if isinstance(result, dict):
            attrs["agentegrity.score.composite"] = float(result.get("composite", 0.0))
            attrs["agentegrity.score.action"] = str(result.get("action", "pass"))
            attrs["agentegrity.score.passed"] = bool(result.get("passed", True))
            attrs["agentegrity.score.confidence"] = float(result.get("confidence", 1.0))
            for prop, val in (result.get("properties") or {}).items():
                attrs[f"agentegrity.score.property.{prop}"] = float(val)
            for layer in result.get("layer_results") or []:
                name = layer.get("layer_name", "unknown")
                attrs[f"agentegrity.layer.{name}.score"] = float(layer.get("score", 0.0))
                attrs[f"agentegrity.layer.{name}.action"] = str(layer.get("action", "pass"))

        # GenAI interop + content hashes (no raw content in attributes).
        tool_name = data.get("tool_name")
        if tool_name:
            attrs["gen_ai.tool.name"] = str(tool_name)
        for key in _CONTENT_KEYS:
            if key in data and data[key] not in (None, "", {}):
                attrs[f"agentegrity.content.{key}_hash"] = _hash(data[key])
        return attrs

    def _record_metrics(self, state: _SessionState, event: dict[str, Any]) -> None:
        result = event.get("evaluation_result")
        if not isinstance(result, dict):
            return
        action = str(result.get("action", "pass"))
        dims: dict[str, Any] = {
            "agentegrity.adapter": state.adapter,
            "agentegrity.action": action,
            "agentegrity.multi_agent": state.multi_agent,
        }
        self._m_evals.add(1, dims)
        self._h_composite.record(
            float(result.get("composite", 0.0)),
            {"agentegrity.adapter": state.adapter},
        )
        self._h_latency.record(
            float(result.get("total_latency_ms", 0.0)),
            {"agentegrity.adapter": state.adapter},
        )
        for layer in result.get("layer_results") or []:
            self._h_layer.record(
                float(layer.get("score", 0.0)),
                {
                    "agentegrity.adapter": state.adapter,
                    "agentegrity.layer": layer.get("layer_name", "unknown"),
                },
            )

    def _record_enforcement_and_health(self,
        span: Any,
        state: _SessionState,
        event_type: str,
        event: dict[str, Any],
    ) -> None:
        result = event.get("evaluation_result")
        if isinstance(result, dict):
            action = str(result.get("action", "pass"))
            if action in _DENYING_ACTIONS:
                self._m_denials.add(
                    1,
                    {
                        "agentegrity.adapter": state.adapter,
                        "agentegrity.action": action,
                    },
                )
                span.add_event(
                    "agentegrity.enforcement.denial",
                    attributes={
                        "agentegrity.score.action": action,
                        "agentegrity.score.composite": float(result.get("composite", 0.0)),
                    },
                )

        if _is_health_event(event_type):
            self._m_health.add(
                1,
                {
                    "agentegrity.adapter": state.adapter,
                    "agentegrity.health.type": event_type,
                },
            )
            span.add_event(f"agentegrity.health.{event_type}")
            span.set_status(Status(StatusCode.ERROR, event_type))

    def _maybe_capture_content(self, span: Any, data: dict[str, Any]) -> None:
        if not self._capture_content:
            return
        for key in _CONTENT_KEYS:
            value = data.get(key)
            if value in (None, "", {}):
                continue
            span.add_event(
                f"agentegrity.content.{key}",
                attributes={"content": str(value)[:_MAX_CONTENT_CHARS]},
            )
