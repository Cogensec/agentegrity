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
from typing import Any

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

    @property
    def canonical_payload(self) -> str:
        """
        The canonical representation of the record used for signing
        and hash computation. Deterministic JSON serialization.
        """
        payload = {
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
        """
        Sign the attestation record with an Ed25519 private key.

        Requires the `cryptography` package.
        """
        if not _HAS_CRYPTO:
            raise ImportError(
                "Cryptographic signing requires the 'cryptography' package. "
                "Install with: pip install agentegrity[crypto]"
            )

        payload_bytes = self.canonical_payload.encode()
        self.signature = private_key.sign(payload_bytes)
        self.public_key = private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    def verify(self, public_key: Any | None = None) -> bool:
        """
        Verify the attestation record's signature.

        Parameters
        ----------
        public_key : Ed25519PublicKey, optional
            If not provided, uses the embedded public key.

        Returns
        -------
        bool
            True if the signature is valid.
        """
        if not _HAS_CRYPTO:
            raise ImportError(
                "Cryptographic verification requires the 'cryptography' package."
            )

        if self.signature is None:
            return False

        if public_key is None:
            if self.public_key is None:
                return False
            public_key = Ed25519PublicKey.from_public_bytes(self.public_key)

        try:
            payload_bytes = self.canonical_payload.encode()
            public_key.verify(self.signature, payload_bytes)
            return True
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
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
        )

    def __repr__(self) -> str:
        signed = "signed" if self.signature else "unsigned"
        score = self.integrity_score.get("composite", "?")
        return f"AttestationRecord({self.record_id[:8]}..., {signed}, score={score})"


class AttestationChain:
    """
    An ordered chain of attestation records for an agent.
    Each record references the hash of the previous record,
    forming a tamper-evident history.
    """

    def __init__(self) -> None:
        self._records: list[AttestationRecord] = []

    def append(self, record: AttestationRecord) -> None:
        """
        Add a record to the chain. Automatically sets chain_previous
        to the hash of the last record in the chain.
        """
        if self._records:
            record.chain_previous = self._records[-1].content_hash
        self._records.append(record)

    def verify_chain(self) -> bool:
        """
        Verify the integrity of the full attestation chain.

        Returns True if every record correctly references the hash
        of its predecessor.
        """
        if not self._records:
            return True

        # First record should have no previous
        if self._records[0].chain_previous is not None:
            return False

        for i in range(1, len(self._records)):
            expected_hash = self._records[i - 1].content_hash
            if self._records[i].chain_previous != expected_hash:
                return False

        return True

    @property
    def records(self) -> list[AttestationRecord]:
        return list(self._records)

    @property
    def latest(self) -> AttestationRecord | None:
        return self._records[-1] if self._records else None

    def to_records_dict(self) -> list[dict[str, Any]]:
        """Serialize every record in the chain via :meth:`AttestationRecord.to_dict`."""
        return [r.to_dict() for r in self._records]

    @classmethod
    def from_records(cls, records: list[AttestationRecord]) -> "AttestationChain":
        """Rebuild a chain from a list of :class:`AttestationRecord` objects.

        The records' existing ``chain_previous`` values are preserved
        verbatim — this is a *restore* operation, not a fresh-append, so
        link hashes from the original chain are kept intact.
        """
        chain = cls()
        chain._records = list(records)
        return chain

    @classmethod
    def from_dict_list(cls, dicts: list[dict[str, Any]]) -> "AttestationChain":
        """Rebuild a chain from a list of ``AttestationRecord.to_dict`` dicts."""
        return cls.from_records([AttestationRecord.from_dict(d) for d in dicts])

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
