"""Shape-only telemetry property builders — the ONLY place event properties are built.

Payloads may carry counts, booleans, enum values, durations, score floats, and
version strings. Never prompts, model I/O, tool arguments, file paths, agent
names, policy text, or exception messages. Documented in ``docs/telemetry.md``.
"""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING, Any

from agentegrity.core.evaluator import PropertyScores

if TYPE_CHECKING:
    from agentegrity.core.attestation import AttestationChain, AttestationRecord
    from agentegrity.core.evaluator import IntegrityScore
    from agentegrity.core.profile import AgentProfile


def _composite_bucket(composite: float) -> str:
    """Bucket a composite score into a coarse label for funnel analysis."""
    if composite < 0.5:
        return "<0.5"
    if composite < 0.7:
        return "0.5-0.7"
    if composite < 0.9:
        return "0.7-0.9"
    return ">=0.9"


def profile_shape(profile: AgentProfile) -> dict[str, Any]:
    """Enum values and capability count only — never the profile name or capabilities."""
    return {
        "risk_tier": profile.risk_tier.value,
        "agent_type": profile.agent_type.value,
        "deployment_context": profile.deployment_context.value,
        "capability_count": len(profile.capabilities),
    }


def score_shape(score: IntegrityScore) -> dict[str, Any]:
    """Per-property floats (3 decimals), composite, bucket, and pass/action outcome."""
    shape: dict[str, Any] = {
        f.name: round(getattr(score.properties, f.name), 3) for f in fields(PropertyScores)
    }
    shape["composite"] = round(score.composite, 3)
    shape["composite_bucket"] = _composite_bucket(score.composite)
    shape["passed"] = score.passed
    shape["action"] = score.action
    return shape


def chain_shape(chain: AttestationChain, *, verified: bool | None = None) -> dict[str, Any]:
    """Record counts and the verification outcome — never record contents."""
    records = chain.records
    return {
        "record_count": len(records),
        "decision_count": sum(1 for r in records if r.record_kind == "decision"),
        "verified": verified,
    }


def record_shape(record: AttestationRecord) -> dict[str, Any]:
    """Kind, signedness, and evidence count of a single attestation record."""
    return {
        "record_kind": record.record_kind,
        "signed": record.signature is not None,
        "evidence_count": len(record.evidence),
    }


def adapter_shape(name: str) -> dict[str, Any]:
    """Adapter name from the fixed _ADAPTER_REGISTRY key set — safe to send."""
    return {"adapter": name}


def lowest_property(score: IntegrityScore) -> str:
    """Name of the lowest-scoring integrity property (violation attribution)."""
    return min(
        (f.name for f in fields(PropertyScores)),
        key=lambda name: getattr(score.properties, name),
    )
