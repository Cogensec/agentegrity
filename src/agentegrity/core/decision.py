"""
Decision provenance - signed, hash-chained records of an agent's
decision rationale at a boundary (pre_tool_use, stop, subagent_start).

Where :class:`agentegrity.core.attestation.AttestationRecord` carries
the integrity evaluator's verdict (what we observed about the agent
from the outside), :class:`DecisionRecord` carries the agent's
candidate action plus the inputs and reasoning that justified it,
captured **before** the action executes. Both record kinds share the
same :class:`AttestationChain` so a single verifier can walk the full
audit trail and confirm decisions weren't retrofitted after the fact.

Three capture tiers describe how much rationale was actually captured
at the boundary. Most adapters today produce Tier C (Minimal) records
because frameworks expose the candidate action but not the agent's
internal deliberation; Tier B (Partial, reasoning chain only) and
Tier A (Full, rejected alternatives) unlock as adapter-specific
deliberation surfaces are wired in.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agentegrity.core.attestation import (
    _sign_canonical,
    _verify_canonical,
)


class CaptureTier(str, Enum):
    """How rich the captured rationale is for a single decision.

    The tier is inferred from which fields the caller populated:

    - ``FULL`` (Tier A): rejected alternatives present. The agent
      considered other actions and rejected them with stated reasons.
    - ``PARTIAL`` (Tier B): reasoning chain present but no rejected
      alternatives.
    - ``MINIMAL`` (Tier C): neither populated. The decision record
      attests the candidate action and inputs only.
    """

    MINIMAL = "minimal"
    PARTIAL = "partial"
    FULL = "full"


def infer_capture_tier(
    reasoning_chain: list[str] | None,
    rejected_alternatives: list["RejectedAlternative"] | None,
) -> CaptureTier:
    """Infer capture tier from which rationale fields are populated."""
    if rejected_alternatives:
        return CaptureTier.FULL
    if reasoning_chain:
        return CaptureTier.PARTIAL
    return CaptureTier.MINIMAL


@dataclass
class DecisionInput:
    """A single input channel that fed into a decision.

    The ``content_hash`` is a SHA-256 over the underlying content; the
    raw text is not stored in the chain. The ``summary`` is a short
    human-readable label for the input ("user_prompt: 'help me ...'").
    """

    channel: str
    content_hash: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "content_hash": self.content_hash,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionInput":
        return cls(
            channel=data["channel"],
            content_hash=data["content_hash"],
            summary=data["summary"],
        )


@dataclass
class RejectedAlternative:
    """An alternative action the agent considered and rejected."""

    action_summary: str
    rejection_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_summary": self.action_summary,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RejectedAlternative":
        return cls(
            action_summary=data["action_summary"],
            rejection_reason=data["rejection_reason"],
        )


def _json_safe(value: Any) -> Any:
    """Coerce a value to something ``json.dumps`` can handle.

    Defensive serialization for ``candidate_action`` and decision
    inputs that may contain non-JSON-native types from adapter
    payloads. Sets become sorted lists, bytes become hex strings,
    dataclasses become dicts, and everything else falls back to
    ``repr()`` with a ``_coerced=True`` marker so a downstream
    verifier knows the value was lossy-encoded.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, set):
        return [_json_safe(v) for v in sorted(value, key=repr)]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    return {"_coerced": True, "repr": repr(value)}


@dataclass
class DecisionRecord:
    """A signed, chain-linked record of one decision the agent made.

    Built at a decision boundary (``pre_tool_use`` / ``stop`` /
    ``subagent_start``) before the action executes, so a downstream
    verifier can prove the rationale was bound at decision time and
    not retrofitted.

    The dataclass mirrors :class:`AttestationRecord`'s shape — same
    ``canonical_payload`` / ``content_hash`` / ``sign`` / ``verify``
    semantics, same chain-link field — so the two record kinds live
    in one :class:`AttestationChain` without dispatch.
    """

    agent_id: str
    decision_point: str
    candidate_action: dict[str, Any]
    decision_inputs: list[DecisionInput] = field(default_factory=list)
    reasoning_chain: list[str] = field(default_factory=list)
    rejected_alternatives: list[RejectedAlternative] = field(default_factory=list)
    goal_state: list[str] = field(default_factory=list)
    capture_tier: CaptureTier = CaptureTier.MINIMAL
    redacted: bool = True
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    chain_previous: str | None = None
    signature: bytes | None = None
    public_key: bytes | None = None
    record_kind: str = "decision"

    @property
    def canonical_payload(self) -> str:
        """Deterministic JSON representation used for signing + hashing."""
        payload = {
            "record_kind": self.record_kind,
            "record_id": self.record_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "decision_point": self.decision_point,
            "capture_tier": self.capture_tier.value,
            "candidate_action": _json_safe(self.candidate_action),
            "decision_inputs": [i.to_dict() for i in self.decision_inputs],
            "reasoning_chain": list(self.reasoning_chain),
            "rejected_alternatives": [
                a.to_dict() for a in self.rejected_alternatives
            ],
            "goal_state": list(self.goal_state),
            "redacted": self.redacted,
            "chain_previous": self.chain_previous,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @property
    def content_hash(self) -> str:
        """SHA-256 hash of the canonical payload."""
        return hashlib.sha256(self.canonical_payload.encode()).hexdigest()

    def sign(self, private_key: Any) -> None:
        """Sign the decision record with an Ed25519 private key."""
        self.signature, self.public_key = _sign_canonical(
            self.canonical_payload, private_key
        )

    def verify(self, public_key: Any | None = None) -> bool:
        """Verify the decision record's signature."""
        return _verify_canonical(
            self.canonical_payload,
            self.signature,
            self.public_key,
            public_key,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_kind": self.record_kind,
            "record_id": self.record_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "decision_point": self.decision_point,
            "capture_tier": self.capture_tier.value,
            "candidate_action": _json_safe(self.candidate_action),
            "decision_inputs": [i.to_dict() for i in self.decision_inputs],
            "reasoning_chain": list(self.reasoning_chain),
            "rejected_alternatives": [
                a.to_dict() for a in self.rejected_alternatives
            ],
            "goal_state": list(self.goal_state),
            "redacted": self.redacted,
            "chain_previous": self.chain_previous,
            "content_hash": self.content_hash,
            "signature": self.signature.hex() if self.signature else None,
            "public_key": self.public_key.hex() if self.public_key else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionRecord":
        """Rebuild a :class:`DecisionRecord` from its ``to_dict``
        representation. ``content_hash`` in the input is ignored —
        it's recomputed from the canonical payload on demand.
        """
        signature = data.get("signature")
        public_key = data.get("public_key")
        return cls(
            agent_id=data["agent_id"],
            decision_point=data["decision_point"],
            candidate_action=data.get("candidate_action", {}),
            decision_inputs=[
                DecisionInput.from_dict(d)
                for d in data.get("decision_inputs", [])
            ],
            reasoning_chain=list(data.get("reasoning_chain", [])),
            rejected_alternatives=[
                RejectedAlternative.from_dict(d)
                for d in data.get("rejected_alternatives", [])
            ],
            goal_state=list(data.get("goal_state", [])),
            capture_tier=CaptureTier(data.get("capture_tier", "minimal")),
            redacted=data.get("redacted", True),
            record_id=data["record_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            chain_previous=data.get("chain_previous"),
            signature=bytes.fromhex(signature) if signature else None,
            public_key=bytes.fromhex(public_key) if public_key else None,
            record_kind=data.get("record_kind", "decision"),
        )

    def __repr__(self) -> str:
        signed = "signed" if self.signature else "unsigned"
        return (
            f"DecisionRecord({self.record_id[:8]}..., "
            f"{self.decision_point}, tier={self.capture_tier.value}, {signed})"
        )


def build_decision_record(
    agent_id: str,
    decision_point: str,
    candidate_action: dict[str, Any],
    *,
    reasoning_chain: list[str] | None = None,
    rejected_alternatives: list[RejectedAlternative] | None = None,
    decision_inputs: list[DecisionInput] | None = None,
    goal_state: list[str] | None = None,
    previous_record_hash: str | None = None,
    signing_key: Any | None = None,
) -> DecisionRecord:
    """Construct a :class:`DecisionRecord`, optionally signed.

    The capture tier is inferred from which rationale fields are
    populated (see :class:`CaptureTier`). The ``chain_previous`` is
    baked into the canonical payload before signing so the signature
    covers the chain link.
    """
    tier = infer_capture_tier(reasoning_chain, rejected_alternatives)
    record = DecisionRecord(
        agent_id=agent_id,
        decision_point=decision_point,
        candidate_action=candidate_action,
        decision_inputs=list(decision_inputs or []),
        reasoning_chain=list(reasoning_chain or []),
        rejected_alternatives=list(rejected_alternatives or []),
        goal_state=list(goal_state or []),
        capture_tier=tier,
        chain_previous=previous_record_hash,
    )
    if signing_key is not None:
        record.sign(signing_key)
    return record
