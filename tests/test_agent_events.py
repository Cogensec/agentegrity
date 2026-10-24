"""Canonical event and capability contract regression and security tests."""

from datetime import datetime, timedelta, timezone

import pytest

from agentegrity.core.agent_events import (
    AgentCapabilityCard,
    CanonicalAgentEvent,
    Capability,
    CapabilityEvidence,
    CapabilityStatus,
    EventSource,
    SourceAssurance,
)


def _event(**kwargs: object) -> CanonicalAgentEvent:
    fields: dict[str, object] = {
        "event_id": "event-1",
        "event_type": "tool.intent",
        "occurred_at": datetime(2026, 10, 22, tzinfo=timezone.utc),
        "agent_id": "agent-1",
        "run_id": "run-1",
        "source": EventSource(host="custom-agent", integration="sdk"),
    }
    fields.update(kwargs)
    return CanonicalAgentEvent(**fields)  # type: ignore[arg-type]


def test_event_round_trip_and_explicit_unverified_source() -> None:
    event = _event(
        session_id="session-1",
        action_id="action-1",
        tool_name="transfer",
        effect="financial_write",
    )
    wire = event.to_dict()
    assert wire["schema_version"] == "1.0"
    assert wire["source"]["assurance"] == "unverified"
    assert "payload" not in wire
    assert "content" not in wire
    assert CanonicalAgentEvent.from_dict(wire).to_dict() == wire


def test_authenticated_label_requires_evidence_but_does_not_verify_it() -> None:
    with pytest.raises(ValueError, match="external evidence"):
        EventSource("host", "sdk", SourceAssurance.AUTHENTICATED)

    source = EventSource(
        "host", "sdk", SourceAssurance.AUTHENTICATED, "signed-record-1"
    )
    assert _event(source=source).to_dict()["source"]["evidence_ref"] == "signed-record-1"


@pytest.mark.parametrize("changes", [
    {"schema_version": "2.0"},
    {"event_type": "tool_intent"},
    {"event_type": "Tool.Intent"},
    {"event_type": "foo..bar"},
    {"event_id": ""},
    {"run_id": " "},
    {"agent_id": "x" * 257},
    {"occurred_at": datetime(2026, 10, 22)},
    {"source": "untrusted"},
])
def test_invalid_event_declaration_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _event(**changes)


@pytest.mark.parametrize("field,value", [
    ("event_id", 42),
    ("action_id", ""),
    ("tool_name", " " ),
    ("parent_event_id", "x" * 257),
])
def test_invalid_identifiers_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _event(**{field: value})


def test_parser_rejects_unknown_fields_and_invalid_time() -> None:
    raw = _event().to_dict()
    with pytest.raises(ValueError, match="unknown canonical"):
        CanonicalAgentEvent.from_dict({**raw, "prompt": "private text"})
    with pytest.raises(ValueError, match="unknown event source"):
        CanonicalAgentEvent.from_dict({
            **raw, "source": {**raw["source"], "secret": "do not log"},
        })
    with pytest.raises(ValueError, match="timezone-aware"):
        CanonicalAgentEvent.from_dict({**raw, "occurred_at": "2026-10-22T12:00:00"})
    with pytest.raises(ValueError, match="timezone-aware"):
        CanonicalAgentEvent.from_dict({**raw, "occurred_at": "tomorrow"})
    with pytest.raises(ValueError, match="object"):
        CanonicalAgentEvent.from_dict([])  # type: ignore[arg-type]


@pytest.mark.parametrize("field", [
    "session_id", "action_id", "parent_event_id", "tool_name", "effect", "outcome",
])
def test_wire_parser_rejects_present_null_optional_fields(field: str) -> None:
    raw = _event().to_dict()
    raw[field] = None
    with pytest.raises(ValueError, match="cannot be null"):
        CanonicalAgentEvent.from_dict(raw)


def test_wire_parser_rejects_null_source_evidence_reference() -> None:
    raw = _event().to_dict()
    raw["source"]["evidence_ref"] = None
    with pytest.raises(ValueError, match="cannot be null"):
        CanonicalAgentEvent.from_dict(raw)


@pytest.mark.parametrize("timestamp", [
    "2026-10-22X10:00:00+00:00",
    "2026-10-22T10:00:00+0000",
    "2026-10-22T10:00:00+00:00:30",
    "2026-02-30T10:00:00Z",
    "2026-10-22T10:00:00.1234567Z",
])
def test_wire_parser_rejects_invalid_or_noncanonical_timestamps(timestamp: str) -> None:
    raw = _event().to_dict()
    raw["occurred_at"] = timestamp
    with pytest.raises(ValueError):
        CanonicalAgentEvent.from_dict(raw)


def test_utc_z_timestamp_is_accepted() -> None:
    raw = _event().to_dict()
    raw["occurred_at"] = "2026-10-22T00:00:00Z"
    assert CanonicalAgentEvent.from_dict(raw).occurred_at.utcoffset().total_seconds() == 0


def test_seconds_level_timezone_is_normalized_before_export() -> None:
    odd_zone = timezone(timedelta(seconds=30))
    event = _event(occurred_at=datetime(2026, 10, 22, tzinfo=odd_zone))
    serialized = event.to_dict()
    assert serialized["occurred_at"] == "2026-10-21T23:59:30+00:00"
    assert CanonicalAgentEvent.from_dict(serialized).to_dict() == serialized


def test_capability_card_requires_all_levels() -> None:
    claims = tuple(
        CapabilityEvidence(
            capability,
            CapabilityStatus.IMPLEMENTED if capability == Capability.OBSERVE
            else CapabilityStatus.UNAVAILABLE,
        )
        for capability in Capability
    )
    card = AgentCapabilityCard("custom-adapter", "custom-host", "1.2", claims)
    serialized = card.to_dict()
    assert len(serialized["claims"]) == 5
    assert serialized["claims"][0] == {
        "capability": "observe", "status": "implemented",
    }
    assert serialized["claims"][2]["status"] == "unavailable"


def test_capability_status_cannot_be_overstated_without_reference() -> None:
    with pytest.raises(ValueError, match="requires evidence"):
        CapabilityEvidence(Capability.AUTHORIZE, CapabilityStatus.VALIDATED)
    with pytest.raises(ValueError, match="cannot claim evidence"):
        CapabilityEvidence(Capability.OBSERVE, CapabilityStatus.UNAVAILABLE, "test")
    claim = CapabilityEvidence(
        Capability.AUTHORIZE, CapabilityStatus.VALIDATED, "tests/test_gate.py"
    )
    assert claim.to_dict()["evidence_ref"] == "tests/test_gate.py"


def test_capability_card_rejects_gaps_and_duplicates() -> None:
    observe = CapabilityEvidence(Capability.OBSERVE, CapabilityStatus.IMPLEMENTED)
    with pytest.raises(ValueError, match="each capability"):
        AgentCapabilityCard("sdk", "host", "1", (observe,))
    with pytest.raises(ValueError, match="duplicate"):
        AgentCapabilityCard("sdk", "host", "1", (observe, observe))


def test_capability_card_rejects_bad_items() -> None:
    with pytest.raises(ValueError, match="tuple"):
        AgentCapabilityCard("sdk", "host", "1", [])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="CapabilityEvidence"):
        AgentCapabilityCard("sdk", "host", "1", ("observe",))  # type: ignore[arg-type]
