"""
Attestation - cryptographic proof of an agent's integrity state.

Attestation records are signed, chained, and independently verifiable.
They transform integrity evaluation from observational ("we checked and
it looked fine") to provable ("here is the signed record you can verify").
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentegrity.core.decision import DecisionRecord
    from agentegrity.core.topology import AgentTopology, TopologyChange

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False


def _sign_canonical(
    canonical: str, private_key: Any
) -> tuple[bytes, bytes]:
    """Sign a canonical payload string with an Ed25519 private key.

    Returns (signature, raw public key bytes). Used by both
    :class:`AttestationRecord` and :class:`DecisionRecord` so the
    signing path lives in exactly one place.
    """
    if not _HAS_CRYPTO:
        raise ImportError(
            "Cryptographic signing requires the 'cryptography' package. "
            "Install with: pip install agentegrity[crypto]"
        )
    signature = private_key.sign(canonical.encode())
    public_key = private_key.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    return signature, public_key


def _verify_canonical(
    canonical: str,
    signature: bytes | None,
    public_key_bytes: bytes | None,
    public_key: Any | None = None,
) -> bool:
    """Verify a canonical payload's signature."""
    if not _HAS_CRYPTO:
        raise ImportError(
            "Cryptographic verification requires the 'cryptography' package."
        )
    if signature is None:
        return False
    if public_key is None:
        if public_key_bytes is None:
            return False
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
    try:
        public_key.verify(signature, canonical.encode())
        return True
    except Exception:
        return False


@runtime_checkable
class ChainedRecord(Protocol):
    """Structural type for any record that can live in an :class:`AttestationChain`.

    Both :class:`AttestationRecord` and :class:`DecisionRecord` satisfy
    this Protocol without inheritance. The chain operates on this
    type, so a heterogeneous chain works without dispatch in the chain
    itself.
    """

    record_kind: str
    chain_previous: str | None
    signature: bytes | None
    public_key: bytes | None

    @property
    def content_hash(self) -> str: ...

    def verify(self, public_key: Any | None = None) -> bool: ...

    def to_dict(self) -> dict[str, Any]: ...


@dataclass
class Evidence:
    """A piece of evidence supporting an attestation."""

    evidence_type: str  # "layer_result" | "validator_output" | "external"
    source: str
    content_hash: str
    summary: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "source": self.source,
            "content_hash": self.content_hash,
            "summary": self.summary,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AttestationRecord:
    """
    A cryptographically signed proof of an agent's integrity state at a
    specific point in time.

    Parameters
    ----------
    agent_id : str
        The agent this attestation covers.
    integrity_score : dict
        The full IntegrityScore as a dictionary.
    layer_states : dict
        Per-layer evaluation states.
    evidence : list[Evidence]
        Supporting evidence chain.
    """

    agent_id: str
    integrity_score: dict[str, Any]
    layer_states: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    chain_previous: str | None = None
    signature: bytes | None = None
    public_key: bytes | None = None
    record_kind: str = "attestation"

    @property
    def canonical_payload(self) -> str:
        """
        The canonical representation of the record used for signing
        and hash computation. Deterministic JSON serialization.
        """
        payload = {
            "record_kind": self.record_kind,
            "record_id": self.record_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "integrity_score": self.integrity_score,
            "layer_states": self.layer_states,
            "evidence": [e.to_dict() for e in self.evidence],
            "chain_previous": self.chain_previous,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @property
    def content_hash(self) -> str:
        """SHA-256 hash of the canonical payload."""
        return hashlib.sha256(self.canonical_payload.encode()).hexdigest()

    def sign(self, private_key: Any) -> None:
        """Sign the attestation record with an Ed25519 private key.

        Requires the `cryptography` package.
        """
        self.signature, self.public_key = _sign_canonical(
            self.canonical_payload, private_key
        )

    def verify(self, public_key: Any | None = None) -> bool:
        """Verify the attestation record's signature.

        Parameters
        ----------
        public_key : Ed25519PublicKey, optional
            If not provided, uses the embedded public key.
        """
        return _verify_canonical(
            self.canonical_payload, self.signature, self.public_key, public_key
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_kind": self.record_kind,
            "record_id": self.record_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "integrity_score": self.integrity_score,
            "layer_states": self.layer_states,
            "evidence": [e.to_dict() for e in self.evidence],
            "chain_previous": self.chain_previous,
            "content_hash": self.content_hash,
            "signature": self.signature.hex() if self.signature else None,
            "public_key": self.public_key.hex() if self.public_key else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttestationRecord":
        """Rebuild an :class:`AttestationRecord` from its ``to_dict``
        representation.

        ``content_hash`` in the input is ignored — it's a derived value
        recomputed from the canonical payload on demand.

        Used by checkpoint backends to round-trip a chain across
        process boundaries (file, sqlite, etc.) without losing
        signatures.
        """
        evidence = [
            Evidence(
                evidence_type=e["evidence_type"],
                source=e["source"],
                content_hash=e["content_hash"],
                summary=e["summary"],
                timestamp=datetime.fromisoformat(e["timestamp"]),
            )
            for e in data.get("evidence", [])
        ]
        signature = data.get("signature")
        public_key = data.get("public_key")
        return cls(
            agent_id=data["agent_id"],
            integrity_score=data["integrity_score"],
            layer_states=data.get("layer_states", {}),
            evidence=evidence,
            record_id=data["record_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            chain_previous=data.get("chain_previous"),
            signature=bytes.fromhex(signature) if signature else None,
            public_key=bytes.fromhex(public_key) if public_key else None,
            record_kind=data.get("record_kind", "attestation"),
        )

    def __repr__(self) -> str:
        signed = "signed" if self.signature else "unsigned"
        score = self.integrity_score.get("composite", "?")
        return f"AttestationRecord({self.record_id[:8]}..., {signed}, score={score})"


class AttestationChain:
    """
    An ordered, tamper-evident chain of records for an agent.

    Holds both :class:`AttestationRecord` (the integrity evaluator's
    verdict) and :class:`DecisionRecord` (the agent's decision rationale
    at a boundary). Each record references the hash of the previous
    record regardless of kind.
    """

    def __init__(self) -> None:
        self._records: list[ChainedRecord] = []

    def append(self, record: ChainedRecord) -> None:
        """Add a record to the chain.

        If the record's ``chain_previous`` is unset, links it to the
        hash of the previous record. If already set (e.g. because a
        signed record was built with the link baked into its canonical
        payload), validates it matches what the chain expects and
        raises ``ValueError`` on mismatch.
        """
        expected_prev = self._records[-1].content_hash if self._records else None
        if record.chain_previous is None:
            record.chain_previous = expected_prev
        elif record.chain_previous != expected_prev:
            raise ValueError(
                f"chain_previous mismatch: record has {record.chain_previous!r}, "
                f"chain expects {expected_prev!r}"
            )
        self._records.append(record)

    def verify_chain(self) -> bool:
        """Verify the integrity of the full chain.

        Returns True iff every record correctly references the hash of
        its predecessor.
        """
        ok, _, _ = self.verify_chain_detailed()
        return ok

    def verify_cross_agent_links(
        self, peer_chains: dict[str, "AttestationChain"] | None = None
    ) -> bool:
        """Verify ``peer_message`` and ``handoff`` Evidence references
        in this chain resolve to real records in peer chains.

        In v0.8 this is a permissive stub returning ``True`` when no
        peer chains are supplied. The full implementation lands in
        v0.9 alongside the :class:`KeyProvider` Protocol and per-agent
        chains (pattern b). The signature exists now so adapters can
        wire it into their multi-agent verification path without
        another API churn next release.

        Parameters
        ----------
        peer_chains : dict[str, AttestationChain], optional
            Map of ``agent_id`` to its chain. When provided, every
            ``peer_message`` / ``handoff`` Evidence in this chain
            must point at a real record in the corresponding peer
            chain whose ``content_hash`` matches.
        """
        if peer_chains is None:
            return True
        for r in self._records:
            if not isinstance(r, AttestationRecord):
                continue
            for ev in r.evidence:
                if ev.evidence_type not in ("peer_message", "handoff"):
                    continue
                # The Evidence.source format for cross-agent links is
                # "<peer_agent_id>:<record_id>" so we can find the
                # right peer chain to walk.
                if ":" not in ev.source:
                    return False
                peer_id, peer_record_id = ev.source.split(":", 1)
                peer_chain = peer_chains.get(peer_id)
                if peer_chain is None:
                    return False
                # Look for the referenced record in the peer chain.
                matched = False
                for peer_record in peer_chain.records:
                    rid = getattr(peer_record, "record_id", None)
                    if rid == peer_record_id:
                        if peer_record.content_hash != ev.content_hash:
                            return False
                        matched = True
                        break
                if not matched:
                    return False
        return True

    def verify_decision_links(self) -> bool:
        """Verify every attestation's decision-type :class:`Evidence`
        entries point at unaltered :class:`DecisionRecord`\\s earlier
        in the chain.

        Returns ``False`` if any of the following holds for any
        attestation:

        * A ``decision``-type Evidence entry references a ``source``
          (decision ``record_id``) that doesn't exist in the chain.
        * The referenced decision sits at or after the attestation
          (decisions must precede the attestation that links them).
        * The referenced decision's current ``content_hash`` doesn't
          match the Evidence ``content_hash`` (the decision was
          tampered after the attestation committed to it).
        """
        decisions_by_id: dict[str, tuple[int, ChainedRecord]] = {}
        for i, r in enumerate(self._records):
            if r.record_kind == "decision":
                decisions_by_id[r.record_id] = (i, r)  # type: ignore[attr-defined]

        for i, r in enumerate(self._records):
            if not isinstance(r, AttestationRecord):
                continue
            for ev in r.evidence:
                if ev.evidence_type != "decision":
                    continue
                entry = decisions_by_id.get(ev.source)
                if entry is None:
                    return False
                decision_idx, decision = entry
                if decision_idx >= i:
                    return False
                if decision.content_hash != ev.content_hash:
                    return False
        return True

    def verify_chain_detailed(
        self,
    ) -> tuple[bool, int | None, str | None]:
        """Like :meth:`verify_chain` but reports the first broken
        record's index and ``record_kind``.

        Returns ``(True, None, None)`` for a valid chain (including
        empty), or ``(False, broken_index, broken_record_kind)``
        otherwise.
        """
        if not self._records:
            return True, None, None
        if self._records[0].chain_previous is not None:
            return False, 0, self._records[0].record_kind
        for i in range(1, len(self._records)):
            expected_hash = self._records[i - 1].content_hash
            if self._records[i].chain_previous != expected_hash:
                return False, i, self._records[i].record_kind
        return True, None, None

    def verify_signatures(
        self, trusted_keys: set[bytes] | None = None
    ) -> tuple[bool, int | None]:
        """Verify every record's Ed25519 signature.

        ``verify_chain`` only proves the records are hash-linked — and
        ``content_hash`` is an unkeyed SHA-256, so anyone who edits a
        record can recompute the links. Hash linkage alone is therefore
        NOT tamper-evidence against an attacker who controls the
        serialized chain. Cryptographic tamper-evidence requires
        verifying signatures, which is what this method does.

        Returns ``(True, None)`` iff every record is signed AND its
        signature verifies. Returns ``(False, first_bad_index)`` on the
        first record that is unsigned or fails verification.

        Parameters
        ----------
        trusted_keys : set[bytes], optional
            Allow-list of raw Ed25519 public keys. When provided, a
            record whose embedded ``public_key`` is not in the set is
            rejected even if its self-embedded signature verifies — this
            is the trust anchor. WITHOUT it, a forged chain signed with
            an attacker-generated key self-verifies, since each record
            carries its own public key. Always pass a pinned key set in
            any context where the chain crosses a trust boundary.
        """
        for i, r in enumerate(self._records):
            if r.signature is None or r.public_key is None:
                return False, i
            if trusted_keys is not None and r.public_key not in trusted_keys:
                return False, i
            if not r.verify():
                return False, i
        return True, None

    @property
    def records(self) -> list[ChainedRecord]:
        return list(self._records)

    @property
    def latest(self) -> ChainedRecord | None:
        return self._records[-1] if self._records else None

    def to_records_dict(self) -> list[dict[str, Any]]:
        """Serialize every record via its ``to_dict()`` method."""
        return [r.to_dict() for r in self._records]

    def to_json(self) -> str:
        """Serialize the full chain to a JSON string."""
        return json.dumps(self.to_records_dict())

    @classmethod
    def from_records(cls, records: list[ChainedRecord]) -> "AttestationChain":
        """Rebuild a chain from a list of record objects.

        The records' existing ``chain_previous`` values are preserved
        verbatim — this is a *restore* operation, not a fresh-append,
        so link hashes from the original chain are kept intact.
        """
        chain = cls()
        chain._records = list(records)
        return chain

    @classmethod
    def from_dict_list(
        cls, dicts: list[dict[str, Any]]
    ) -> "AttestationChain":
        """Rebuild a chain from a list of ``to_dict`` dicts.

        Dispatches on each dict's ``record_kind`` (defaulting to
        ``"attestation"`` for backward compatibility with chains
        serialized before the field existed).
        """
        from agentegrity.core.decision import DecisionRecord

        records: list[ChainedRecord] = []
        for d in dicts:
            kind = d.get("record_kind", "attestation")
            if kind == "attestation":
                records.append(AttestationRecord.from_dict(d))
            elif kind == "decision":
                records.append(DecisionRecord.from_dict(d))
            else:
                raise ValueError(f"Unknown record_kind: {kind!r}")
        return cls.from_records(records)

    @classmethod
    def from_json(cls, text: str) -> "AttestationChain":
        """Rebuild a chain from a JSON string produced by :meth:`to_json`."""
        return cls.from_dict_list(json.loads(text))

    def __len__(self) -> int:
        return len(self._records)

    def __repr__(self) -> str:
        return f"AttestationChain(records={len(self._records)})"


def generate_signing_key() -> Any:
    """
    Generate a new Ed25519 private key for attestation signing.

    Returns
    -------
    Ed25519PrivateKey
        A new signing key.
    """
    if not _HAS_CRYPTO:
        raise ImportError(
            "Key generation requires the 'cryptography' package. "
            "Install with: pip install agentegrity[crypto]"
        )
    return Ed25519PrivateKey.generate()


def _layer_result_evidence(layer_result: Any) -> Evidence:
    """Build a deterministic Evidence entry from a LayerResult.

    The ``content_hash`` is a real SHA-256 over the canonical JSON of
    the layer-result dict — deterministic across processes, unlike the
    previous ``str(hash(...))`` which used Python's process-salted
    string hash.
    """
    canonical = json.dumps(
        layer_result.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return Evidence(
        evidence_type="layer_result",
        source=layer_result.layer_name,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        summary=(
            f"{layer_result.layer_name}: "
            f"{layer_result.score:.3f} ({layer_result.action})"
        ),
    )


def build_attestation_record(
    profile: Any,
    score: Any,
    *,
    previous_record_hash: str | None = None,
    signing_key: Any | None = None,
    recent_decisions: list["DecisionRecord"] | None = None,
    topology: "AgentTopology | None" = None,
    topology_change: "TopologyChange | None" = None,
) -> AttestationRecord:
    """Construct an :class:`AttestationRecord` from a profile + score.

    Consolidates the record-construction logic that was previously
    duplicated across the adapter base, the standalone monitor, and the
    high-level SDK client. Used by all three.

    Parameters
    ----------
    profile : AgentProfile
        The evaluated agent.
    score : IntegrityScore
        The evaluation result; one Evidence entry is produced per
        ``score.layer_results`` entry.
    previous_record_hash : str, optional
        If provided, baked into the record's ``chain_previous`` before
        signing. Pass the previous record's ``content_hash`` when
        building a record destined to extend an existing chain so the
        signature covers the chain link.
    signing_key : Ed25519PrivateKey, optional
        If provided, the record is signed in place before return.
    recent_decisions : list[DecisionRecord], optional
        Decision records appended to the chain since the previous
        attestation. Each contributes an ``Evidence`` entry of type
        ``"decision"`` so the attestation cryptographically commits to
        the rationales that preceded it.
    topology : AgentTopology, optional
        The in-process multi-agent topology snapshot live at
        evaluation time. Contributes an ``Evidence`` entry of type
        ``"topology"`` so the attestation commits to the structural
        shape the agent participated in.
    topology_change : TopologyChange, optional
        A diff against the previous topology snapshot, emitted when
        the topology mutated since the last attestation. Contributes
        an ``Evidence`` entry of type ``"topology_change"``.
    """
    evidence = [_layer_result_evidence(r) for r in score.layer_results]
    if recent_decisions:
        for d in recent_decisions:
            evidence.append(Evidence(
                evidence_type="decision",
                source=d.record_id,
                content_hash=d.content_hash,
                summary=f"{d.decision_point}: {d.capture_tier.value}",
            ))
    if topology is not None:
        evidence.append(Evidence(
            evidence_type="topology",
            source=topology.topology_id,
            content_hash=topology.content_hash(),
            summary=f"{topology.kind.value}: {len(topology.members)} members",
        ))
    if topology_change is not None:
        evidence.append(Evidence(
            evidence_type="topology_change",
            source=topology_change.previous_topology_id,
            content_hash=topology_change.new_content_hash,
            summary=(
                f"+{len(topology_change.added_members)} "
                f"-{len(topology_change.removed_member_ids)} members"
            ),
        ))
    record = AttestationRecord(
        agent_id=profile.agent_id,
        integrity_score=score.to_dict(),
        layer_states={r.layer_name: r.to_dict() for r in score.layer_results},
        evidence=evidence,
        chain_previous=previous_record_hash,
    )
    if signing_key is not None:
        record.sign(signing_key)
    return record
