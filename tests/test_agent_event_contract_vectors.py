"""Shared JSON contract vectors and Draft 2020-12 schema validation."""

import json
from datetime import datetime
from pathlib import Path

import jsonschema
import pytest

from agentegrity.core.agent_events import (
    AgentCapabilityCard,
    CanonicalAgentEvent,
    Capability,
    CapabilityEvidence,
    CapabilityStatus,
)

_ROOT = Path(__file__).resolve().parent.parent
_VECTORS = json.loads(
    (_ROOT / "tests/fixtures/agent_event_contract_v1.json").read_text(encoding="utf-8")
)
_EVENT_SCHEMA = json.loads(
    (_ROOT / "schemas/agent-event-v1.json").read_text(encoding="utf-8")
)
_CARD_SCHEMA = json.loads(
    (_ROOT / "schemas/agent-capabilities-v1.json").read_text(encoding="utf-8")
)
_FORMAT = jsonschema.FormatChecker()


@_FORMAT.checks("date-time")
def _check_calendar_date(value: object) -> bool:
    """The default format checker accepts impossible days on some runtimes.

    Require calendar validity in addition to the schema's strict timestamp
    pattern; this mirrors production boundary parsing.
    """
    if not isinstance(value, str):
        return True
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True



@pytest.mark.parametrize("event", _VECTORS["valid_events"])
def test_valid_events_pass_python_and_schema(event: dict) -> None:
    jsonschema.validate(event, _EVENT_SCHEMA, format_checker=_FORMAT)
    assert CanonicalAgentEvent.from_dict(event).to_dict() == (
        {**event, "occurred_at": event["occurred_at"].replace("Z", "+00:00")}
    )


@pytest.mark.parametrize("event", _VECTORS["invalid_events"])
def test_invalid_events_fail_python_and_schema(event: dict) -> None:
    with pytest.raises(ValueError):
        CanonicalAgentEvent.from_dict(event)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(event, _EVENT_SCHEMA, format_checker=_FORMAT)


@pytest.mark.parametrize("card", _VECTORS["valid_cards"])
def test_valid_cards_pass_python_and_schema(card: dict) -> None:
    jsonschema.validate(card, _CARD_SCHEMA, format_checker=_FORMAT)
    parsed = AgentCapabilityCard(
        integration=card["integration"],
        host=card["host"],
        host_version=card["host_version"],
        claims=tuple(
            CapabilityEvidence(
                Capability(claim["capability"]),
                CapabilityStatus(claim["status"]),
                claim.get("evidence_ref"),
            )
            for claim in card["claims"]
        ),
    )
    assert parsed.to_dict() == card


@pytest.mark.parametrize("card", _VECTORS["invalid_cards"])
def test_invalid_cards_fail_schema(card: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(card, _CARD_SCHEMA, format_checker=_FORMAT)
