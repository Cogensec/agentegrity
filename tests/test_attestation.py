"""Tests for AttestationRecord and AttestationChain."""


from agentegrity.core.attestation import (
    AttestationChain,
    AttestationRecord,
    Evidence,
)


def make_record(agent_id="agent-001", score=0.85):
    return AttestationRecord(
        agent_id=agent_id,
        integrity_score={"composite": score, "passed": True},
        layer_states={"adversarial": {"score": 0.90}},
        evidence=[
            Evidence(
                evidence_type="layer_result",
                source="adversarial",
                content_hash="abc123",
                summary="adversarial: 0.90 (pass)",
            )
        ],
    )


class TestAttestationRecord:
    def test_creation(self):
        record = make_record()
        assert record.agent_id == "agent-001"
        assert record.record_id
        assert record.timestamp

    def test_canonical_payload_deterministic(self):
        record = make_record()
        p1 = record.canonical_payload
        p2 = record.canonical_payload
        assert p1 == p2

    def test_content_hash(self):
        record = make_record()
        h = record.content_hash
        assert len(h) == 64  # SHA-256 hex digest
        # Same record produces same hash
        assert record.content_hash == h

    def test_different_records_different_hashes(self):
        r1 = make_record(agent_id="agent-001")
        r2 = make_record(agent_id="agent-002")
        assert r1.content_hash != r2.content_hash

    def test_serialization(self):
        record = make_record()
        d = record.to_dict()
        assert d["agent_id"] == "agent-001"
        assert d["content_hash"]
        assert d["signature"] is None  # Unsigned

    def test_unsigned_verify_returns_false(self):
        """Verifying an unsigned record should return False, not crash."""
        record = make_record()
        try:
            result = record.verify()
            assert result is False
        except ImportError:
            # OK if cryptography not installed
            pass


class TestAttestationChain:
    def test_empty_chain_verifies(self):
        chain = AttestationChain()
        assert chain.verify_chain()
        assert len(chain) == 0
        assert chain.latest is None

    def test_single_record(self):
        chain = AttestationChain()
        record = make_record()
        chain.append(record)
        assert len(chain) == 1
        assert chain.latest is record
        assert record.chain_previous is None
        assert chain.verify_chain()

    def test_chain_linking(self):
        chain = AttestationChain()
        r1 = make_record(score=0.90)
        r2 = make_record(score=0.85)
        r3 = make_record(score=0.88)

        chain.append(r1)
        chain.append(r2)
        chain.append(r3)

        assert r1.chain_previous is None
        assert r2.chain_previous == r1.content_hash
        assert r3.chain_previous == r2.content_hash
        assert chain.verify_chain()

    def test_tampered_chain_fails_verification(self):
        chain = AttestationChain()
        r1 = make_record(score=0.90)
        r2 = make_record(score=0.85)

        chain.append(r1)
        chain.append(r2)

        # Tamper with chain_previous
        r2.chain_previous = "tampered_hash"
        assert not chain.verify_chain()

    def test_records_property_returns_copy(self):
        chain = AttestationChain()
        chain.append(make_record())
        records = chain.records
        records.append(make_record())  # Modify the copy
        assert len(chain) == 1  # Original unchanged
