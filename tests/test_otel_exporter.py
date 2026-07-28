"""Tests for the OpenTelemetry session exporter.

Drives synthetic session-exporter callback streams through
``OTelSessionExporter`` against in-memory OTel span and metric readers,
asserting the emitted traces, attributes, span events, and metrics. No
live collector or OTLP endpoint is needed.

Skipped when the [otel] extra (opentelemetry-sdk) is not installed.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentegrity.exporters.otel import OTelSessionExporter

SESSION = "sess-1"


def _profile() -> dict:
    return {"agent_id": "agent-7", "name": "researcher", "risk_tier": "medium"}


def _score(composite: float, action: str = "pass") -> dict:
    return {
        "composite": composite,
        "action": action,
        "passed": action == "pass",
        "confidence": 1.0,
        "total_latency_ms": 12.5,
        "properties": {"adversarial_coherence": composite, "recovery_integrity": 0.9},
        "layer_results": [
            {"layer_name": "adversarial", "score": composite, "action": action},
            {"layer_name": "recovery", "score": 0.9, "action": "pass"},
        ],
    }


def _evt(event_type: str, *, data: dict | None = None, result: dict | None = None) -> dict:
    return {
        "event_type": event_type,
        "adapter_name": "bedrock_agents",
        "data": data or {},
        "evaluation_result": result,
    }


def _build(**kwargs):
    """Exporter wired to in-memory span + metric readers."""
    span_exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(span_exporter))
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    exporter = OTelSessionExporter(
        tracer_provider=tp, meter_provider=mp, **kwargs
    )
    return exporter, span_exporter, reader


def _spans_by_name(span_exporter):
    return {s.name: s for s in span_exporter.get_finished_spans()}


def _metric_values(reader):
    """name -> list[(attributes_dict, value_or_count)] across all data points."""
    out: dict[str, list] = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                for dp in metric.data.data_points:
                    value = getattr(dp, "value", None)
                    if value is None:
                        value = getattr(dp, "count", None)
                    out.setdefault(metric.name, []).append((dict(dp.attributes), value))
    return out


async def _drive(exporter: OTelSessionExporter) -> None:
    await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
    # Two-member topology -> multi_agent True.
    await exporter.on_event(
        SESSION,
        _evt(
            "topology_declared",
            data={
                "topology": {
                    "kind": "hub_spoke",
                    "members": [{"agent_id": "a"}, {"agent_id": "b"}],
                }
            },
            result=_score(0.95),
        ),
    )
    # A blocked tool call with raw content that must be hashed, not stored.
    await exporter.on_event(
        SESSION,
        _evt(
            "pre_tool_use",
            data={"tool_name": "search", "tool_input": {"q": "secret query"}},
            result=_score(0.40, action="block"),
        ),
    )
    # Instrumentation-health events (no evaluation).
    await exporter.on_event(SESSION, _evt("capture_failure", data={"decision_point": "stop"}))
    await exporter.on_event(
        SESSION, _evt("peer_messages_overflow", data={"channel": "peer_messages", "limit": 1000})
    )
    await exporter.on_session_end(
        SESSION,
        {
            "chain_hash_linked": True,
            "evaluations": 2,
            "attestation_records": 2,
            "decision_records": 1,
            "enforce_mode": True,
        },
    )


@pytest.fixture
def harness():
    exporter, span_exporter, reader = _build()
    asyncio.run(_drive(exporter))
    return span_exporter, reader


# --- traces ---


def test_session_and_event_spans_emitted(harness):
    span_exporter, _ = harness
    spans = _spans_by_name(span_exporter)
    assert "agentegrity.session" in spans
    assert "agentegrity.topology_declared" in spans
    assert "agentegrity.pre_tool_use" in spans
    # Children are parented under the session root.
    root = spans["agentegrity.session"]
    child = spans["agentegrity.pre_tool_use"]
    assert child.parent is not None
    assert child.parent.span_id == root.context.span_id


def test_score_and_topology_attributes(harness):
    span_exporter, _ = harness
    spans = _spans_by_name(span_exporter)

    topo = spans["agentegrity.topology_declared"]
    assert topo.attributes["agentegrity.topology.kind"] == "hub_spoke"
    assert topo.attributes["agentegrity.topology.member_count"] == 2
    assert topo.attributes["agentegrity.topology.multi_agent"] is True

    tool = spans["agentegrity.pre_tool_use"]
    assert tool.attributes["agentegrity.score.composite"] == pytest.approx(0.40)
    assert tool.attributes["agentegrity.score.action"] == "block"
    assert tool.attributes["agentegrity.layer.adversarial.score"] == pytest.approx(0.40)
    assert tool.attributes["gen_ai.tool.name"] == "search"


def test_single_member_topology_not_flagged_multi_agent():
    """A degenerate one-member topology must not inflate the fleet view."""
    exporter, span_exporter, _ = _build()

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_event(
            SESSION,
            _evt(
                "topology_declared",
                data={"topology": {"kind": "hub_spoke", "members": [{"agent_id": "solo"}]}},
                result=_score(0.9),
            ),
        )

    asyncio.run(drive())
    topo = _spans_by_name(span_exporter)["agentegrity.topology_declared"]
    assert topo.attributes["agentegrity.topology.member_count"] == 1
    assert topo.attributes["agentegrity.topology.multi_agent"] is False


def test_raw_content_is_hashed_not_stored(harness):
    span_exporter, _ = harness
    tool = _spans_by_name(span_exporter)["agentegrity.pre_tool_use"]
    assert "agentegrity.content.tool_input_hash" in tool.attributes
    # The raw query never appears in any attribute value.
    assert all("secret query" not in str(v) for v in tool.attributes.values())


def test_capture_content_opt_in_adds_span_event():
    exporter, span_exporter, _ = _build(capture_content=True)

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_event(
            SESSION,
            _evt("user_prompt_submit", data={"prompt": "hello world"}, result=_score(0.9)),
        )

    asyncio.run(drive())
    span = _spans_by_name(span_exporter)["agentegrity.user_prompt_submit"]
    events = {e.name: e for e in span.events}
    assert "agentegrity.content.prompt" in events
    assert events["agentegrity.content.prompt"].attributes["content"] == "hello world"


def test_enforcement_denial_span_event(harness):
    span_exporter, _ = harness
    tool = _spans_by_name(span_exporter)["agentegrity.pre_tool_use"]
    assert any(e.name == "agentegrity.enforcement.denial" for e in tool.events)


def test_escalate_counts_as_denial():
    """v0.8.1: escalate fails closed under enforcement, so it is a denial."""
    exporter, span_exporter, reader = _build()

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_event(
            SESSION, _evt("pre_tool_use", result=_score(0.6, action="escalate"))
        )

    asyncio.run(drive())
    denials = _metric_values(reader).get("agentegrity.enforcement.denials", [])
    assert sum(v for _, v in denials) == 1
    assert any(a.get("agentegrity.action") == "escalate" for a, _ in denials)


def test_health_events_marked_error(harness):
    """capture_failure and the dynamic <channel>_overflow family both count."""
    span_exporter, reader = harness
    spans = _spans_by_name(span_exporter)
    for name in ("agentegrity.capture_failure", "agentegrity.peer_messages_overflow"):
        span = spans[name]
        assert span.status.status_code.name == "ERROR"
        assert any(e.name.startswith("agentegrity.health.") for e in span.events)
    health = sum(v for _, v in _metric_values(reader).get("agentegrity.health_events", []))
    assert health == 2


# --- chain integrity (v0.8.1 trust model) ---


def test_hash_linked_reported_without_error_status(harness):
    """Hash linkage is not tamper-evidence, so a root span carrying it
    alone is never marked ERROR."""
    span_exporter, _ = harness
    root = _spans_by_name(span_exporter)["agentegrity.session"]
    assert root.attributes["agentegrity.chain.hash_linked"] is True
    assert root.attributes["agentegrity.evaluations"] == 2
    assert root.attributes["agentegrity.enforce_mode"] is True
    # No signature check was configured -> attribute absent, not False.
    assert "agentegrity.chain.signatures_verified" not in root.attributes


def test_broken_hash_linkage_does_not_error_the_span():
    exporter, span_exporter, _ = _build()

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_session_end(SESSION, {"chain_hash_linked": False})

    asyncio.run(drive())
    root = _spans_by_name(span_exporter)["agentegrity.session"]
    assert root.attributes["agentegrity.chain.hash_linked"] is False
    assert root.status.status_code.name != "ERROR"


class _FakeChain:
    def __init__(self, verified: bool, bad_index: int | None = None) -> None:
        self._verified = verified
        self._bad_index = bad_index
        self.seen_keys: object = "unset"

    def verify_signatures(self, trusted_keys=None):
        self.seen_keys = trusted_keys
        return self._verified, self._bad_index


def test_signatures_verified_emitted_with_trust_anchor():
    chain = _FakeChain(True)
    keys = {b"pinned-key"}
    exporter, span_exporter, _ = _build(
        chain_provider=lambda: chain, trusted_keys=keys
    )

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_session_end(SESSION, {"chain_hash_linked": True})

    asyncio.run(drive())
    root = _spans_by_name(span_exporter)["agentegrity.session"]
    assert root.attributes["agentegrity.chain.signatures_verified"] is True
    assert root.attributes["agentegrity.chain.trust_anchored"] is True
    assert chain.seen_keys == keys
    assert root.status.status_code.name != "ERROR"


def test_failed_signature_verification_errors_the_span():
    exporter, span_exporter, _ = _build(chain_provider=lambda: _FakeChain(False, 3))

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_session_end(SESSION, {"chain_hash_linked": True})

    asyncio.run(drive())
    root = _spans_by_name(span_exporter)["agentegrity.session"]
    assert root.attributes["agentegrity.chain.signatures_verified"] is False
    assert root.attributes["agentegrity.chain.first_bad_record"] == 3
    # Unpinned verification is not a real trust anchor; surface that.
    assert root.attributes["agentegrity.chain.trust_anchored"] is False
    assert root.status.status_code.name == "ERROR"


def test_raising_chain_provider_is_fail_open():
    def boom():
        raise RuntimeError("chain unavailable")

    exporter, span_exporter, _ = _build(chain_provider=boom)

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        await exporter.on_session_end(SESSION, {"chain_hash_linked": True})

    asyncio.run(drive())
    root = _spans_by_name(span_exporter)["agentegrity.session"]
    # Session still closes cleanly; signature attribute simply absent.
    assert "agentegrity.chain.signatures_verified" not in root.attributes


# --- metrics ---


def test_metrics_recorded(harness):
    _, reader = harness
    metrics = _metric_values(reader)
    assert "agentegrity.evaluations" in metrics
    assert "agentegrity.score.composite" in metrics
    assert "agentegrity.score.layer" in metrics
    denials = sum(v for _, v in metrics.get("agentegrity.enforcement.denials", []))
    assert denials == 1
    # Active sessions returned to zero after session_end (+1 then -1).
    active = sum(v for _, v in metrics.get("agentegrity.sessions.active", []))
    assert active == 0


# --- resilience ---


def test_event_without_session_start_opens_lazy_session():
    """Exporter registered late must not drop events."""
    exporter, span_exporter, _ = _build()
    asyncio.run(exporter.on_event(SESSION, _evt("stop", result=_score(0.9))))
    spans = _spans_by_name(span_exporter)
    assert "agentegrity.stop" in spans
    assert exporter._sessions[SESSION].root_span.attributes["agentegrity.session.lazy"] is True


def test_unknown_session_end_is_ignored():
    exporter, span_exporter, _ = _build()
    asyncio.run(exporter.on_session_end("never-started", {"chain_hash_linked": True}))
    assert span_exporter.get_finished_spans() == ()


def test_malformed_event_is_fail_open():
    """A broken event must never propagate to the instrumented agent."""
    exporter, _, _ = _build()

    async def drive():
        await exporter.on_session_start(SESSION, "bedrock_agents", _profile())
        # evaluation_result is the wrong type; data is not a dict.
        await exporter.on_event(SESSION, {"event_type": "stop", "data": None,
                                          "evaluation_result": "not-a-dict"})

    asyncio.run(drive())  # must not raise


def test_import_error_without_otel(monkeypatch):
    """Constructor gives a helpful install hint when otel is unavailable."""
    import agentegrity.exporters.otel as mod

    monkeypatch.setattr(mod, "_OTEL_AVAILABLE", False)
    with pytest.raises(ImportError, match=r"agentegrity\[otel\]"):
        mod.OTelSessionExporter()
