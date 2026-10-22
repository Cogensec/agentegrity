"""Versioned, framework-independent event and capability declarations.

This is an opt-in interoperability contract. It does not intercept tools,
authenticate events, evaluate policies, or verify external effects.
Host-provided identity and assurance claims are never inferred from agent text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

EVENT_SCHEMA_VERSION = "1.0"
_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_MAX_IDENTIFIER = 256


class SourceAssurance(str, Enum):
    """The integration's declared event-source authentication state."""

    UNVERIFIED = "unverified"
    HOST_ASSERTED = "host_asserted"
    AUTHENTICATED = "authenticated"


class Capability(str, Enum):
    OBSERVE = "observe"
    EVALUATE = "evaluate"
    AUTHORIZE = "authorize"
    VERIFY_EFFECT = "verify_effect"
    RECOVER = "recover"


class CapabilityStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    IMPLEMENTED = "implemented"
    VALIDATED = "validated"


def _identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_IDENTIFIER:
        raise ValueError(f"{label} must be a nonempty string up to {_MAX_IDENTIFIER} characters")


def _optional_identifier(value: str | None, label: str) -> None:
    if value is not None:
        _identifier(value, label)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class EventSource:
    """Origin labeling supplied by a trusted integration, never an LLM.

    AUTHENTICATED is a *reported* status, not proof validated by this SDK.
    The receiving system must verify source identity independently.
    """

    host: str
    integration: str
    assurance: SourceAssurance = SourceAssurance.UNVERIFIED
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.host, "host")
        _identifier(self.integration, "integration")
        if not isinstance(self.assurance, SourceAssurance):
            raise ValueError("assurance must be a SourceAssurance")
        _optional_identifier(self.evidence_ref, "evidence_ref")
        if self.assurance == SourceAssurance.AUTHENTICATED and self.evidence_ref is None:
            raise ValueError("authenticated source requires a reference to external evidence")

    def to_dict(self) -> dict[str, str]:
        result = {
            "host": self.host,
            "integration": self.integration,
            "assurance": self.assurance.value,
        }
        if self.evidence_ref is not None:
            result["evidence_ref"] = self.evidence_ref
        return result


@dataclass(frozen=True)
class CanonicalAgentEvent:
    """Portable metadata-only agent event; payload contents are excluded.

    The event_id and correlation IDs are opaque, not trusted identifiers.
    Neither this class nor its JSON representation performs authentication.
    """

    event_id: str
    event_type: str
    occurred_at: datetime
    agent_id: str
    run_id: str
    source: EventSource
    session_id: str | None = None
    action_id: str | None = None
    parent_event_id: str | None = None
    tool_name: str | None = None
    effect: str | None = None
    outcome: str | None = None
    schema_version: str = field(default=EVENT_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported canonical event schema version")
        for name in ("event_id", "agent_id", "run_id"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.event_type, str) or not _EVENT_NAME.fullmatch(self.event_type):
            raise ValueError("event_type must use dot-separated lowercase tokens")
        if not isinstance(self.occurred_at, datetime) or not _aware(self.occurred_at):
            raise ValueError("occurred_at must be timezone-aware")
        if not isinstance(self.source, EventSource):
            raise ValueError("source must be an EventSource")
        for name in (
            "session_id", "action_id", "parent_event_id",
            "tool_name", "effect", "outcome",
        ):
            _optional_identifier(getattr(self, name), name)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "agent_id": self.agent_id,
            "run_id": self.run_id,
            "source": self.source.to_dict(),
        }
        for name in (
            "session_id", "action_id", "parent_event_id",
            "tool_name", "effect", "outcome",
        ):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CanonicalAgentEvent:
        """Parse a boundary event; reject unknown keys and malformed values."""
        if not isinstance(raw, Mapping):
            raise ValueError("canonical event must be an object")
        allowed = {
            "schema_version", "event_id", "event_type", "occurred_at",
            "agent_id", "run_id", "source", "session_id", "action_id",
            "parent_event_id", "tool_name", "effect", "outcome",
        }
        if set(raw) - allowed:
            raise ValueError("unknown canonical event fields")
        source = raw.get("source")
        if not isinstance(source, Mapping):
            raise ValueError("source must be an object")
        if set(source) - {"host", "integration", "assurance", "evidence_ref"}:
            raise ValueError("unknown event source fields")
        timestamp = raw.get("occurred_at")
        if not isinstance(timestamp, str):
            raise ValueError("occurred_at must be an ISO 8601 string")
        try:
            occurred_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid occurred_at timestamp") from exc
        try:
            assurance = SourceAssurance(source["assurance"])
            parsed_source = EventSource(
                host=source["host"],
                integration=source["integration"],
                assurance=assurance,
                evidence_ref=source.get("evidence_ref"),
            )
            return cls(
                schema_version=raw["schema_version"],
                event_id=raw["event_id"],
                event_type=raw["event_type"],
                occurred_at=occurred_at,
                agent_id=raw["agent_id"],
                run_id=raw["run_id"],
                source=parsed_source,
                **{key: raw[key] for key in (
                    "session_id", "action_id", "parent_event_id",
                    "tool_name", "effect", "outcome",
                ) if key in raw},
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid canonical event fields") from exc


@dataclass(frozen=True)
class CapabilityEvidence:
    """One host-specific capability claim, with optional validation reference."""

    capability: Capability
    status: CapabilityStatus
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability, Capability):
            raise ValueError("capability must be a Capability")
        if not isinstance(self.status, CapabilityStatus):
            raise ValueError("status must be a CapabilityStatus")
        _optional_identifier(self.evidence_ref, "evidence_ref")
        if self.status == CapabilityStatus.VALIDATED and self.evidence_ref is None:
            raise ValueError("validated capability requires evidence_ref")
        if self.status == CapabilityStatus.UNAVAILABLE and self.evidence_ref is not None:
            raise ValueError("unavailable capability cannot claim evidence")

    def to_dict(self) -> dict[str, str]:
        result = {"capability": self.capability.value, "status": self.status.value}
        if self.evidence_ref is not None:
            result["evidence_ref"] = self.evidence_ref
        return result


@dataclass(frozen=True)
class AgentCapabilityCard:
    """Version-scoped declarations; not an independent certification."""

    integration: str
    host: str
    host_version: str
    claims: tuple[CapabilityEvidence, ...]

    def __post_init__(self) -> None:
        for key in ("integration", "host", "host_version"):
            _identifier(getattr(self, key), key)
        if not isinstance(self.claims, tuple):
            raise ValueError("claims must be a tuple")
        capabilities: set[Capability] = set()
        for claim in self.claims:
            if not isinstance(claim, CapabilityEvidence):
                raise ValueError("claims must contain CapabilityEvidence")
            if claim.capability in capabilities:
                raise ValueError("duplicate capability")
            capabilities.add(claim.capability)
        if capabilities != set(Capability):
            raise ValueError("declare each capability, including unavailable ones")

    def to_dict(self) -> dict[str, Any]:
        return {
            "integration": self.integration,
            "host": self.host,
            "host_version": self.host_version,
            "claims": [claim.to_dict() for claim in self.claims],
        }
