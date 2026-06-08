"""Tests for AttestationRecord and AttestationChain."""

import subprocess
import sys
import textwrap

import pytest

from agentegrity.core.attestation import (
    AttestationChain,
    AttestationRecord,
    Evidence,
    build_attestation_record,
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

    def test_append_preserves_preset_chain_previous(self):
        """A record whose chain_previous matches expectation is kept verbatim.

        Records built with the chain link baked into their canonical
        payload (so the signature covers the link) must not have their
        chain_previous overwritten on append.
        """
        chain = AttestationChain()
        r1 = make_record(score=0.90)
        chain.append(r1)
        r2 = make_record(score=0.85)
        r2.chain_previous = r1.content_hash
        chain.append(r2)  # should not raise, should not overwrite
        assert r2.chain_previous == r1.content_hash
        assert chain.verify_chain()

    def test_append_rejects_chain_previous_mismatch(self):
        """A record with a wrong chain_previous raises rather than corrupting."""
        chain = AttestationChain()
        r1 = make_record(score=0.90)
        chain.append(r1)
        r2 = make_record(score=0.85)
        r2.chain_previous = "wrong_hash_value"
        with pytest.raises(ValueError, match="chain_previous mismatch"):
            chain.append(r2)


class TestBuildAttestationRecordHelper:
    """The build_attestation_record helper replaces three duplicated bodies
    in adapter base, monitor, and SDK client. Critically, Evidence
    content_hash is now real SHA-256 — deterministic across processes —
    instead of Python's process-salted str(hash(...)).
    """

    def _stub_score(self, layer_score=0.9, action="pass"):
        from agentegrity.core.evaluator import (
            IntegrityScore,
            LayerResult,
            PropertyScores,
        )

        return IntegrityScore(
            composite=0.85,
            properties=PropertyScores(adversarial_coherence=0.9),
            layer_results=[
                LayerResult(
                    layer_name="adversarial",
                    score=layer_score,
                    passed=action == "pass",
                    action=action,
                    details={"matches": 0},
                    latency_ms=12.3,
                )
            ],
        )

    def _stub_profile(self):
        from agentegrity.core.profile import (
            AgentProfile,
            AgentType,
            DeploymentContext,
            RiskTier,
        )

        return AgentProfile(
            name="phase0-test",
            agent_type=AgentType.TOOL_USING,
            capabilities=["tool_use"],
            deployment_context=DeploymentContext.CLOUD,
            risk_tier=RiskTier.MEDIUM,
        )

    def test_helper_produces_evidence_per_layer(self):
        profile = self._stub_profile()
        score = self._stub_score()
        record = build_attestation_record(profile, score)
        assert len(record.evidence) == 1
        assert record.evidence[0].evidence_type == "layer_result"
        assert record.evidence[0].source == "adversarial"
        assert len(record.evidence[0].content_hash) == 64  # SHA-256 hex

    def test_evidence_content_hash_is_deterministic_in_process(self):
        profile = self._stub_profile()
        score = self._stub_score()
        r1 = build_attestation_record(profile, score)
        r2 = build_attestation_record(profile, score)
        assert r1.evidence[0].content_hash == r2.evidence[0].content_hash

    def test_evidence_content_hash_is_deterministic_across_processes(self):
        """The defect this replaces was process-salted Python hash().

        Run two subprocesses, build the same layer-result-derived
        Evidence in each, and compare. Identical input → identical
        hash. With the old code the values would differ run-to-run
        because PYTHONHASHSEED is randomized per process.
        """
        script = textwrap.dedent(
            """
            from agentegrity.core.attestation import build_attestation_record
            from agentegrity.core.evaluator import (
                IntegrityScore, LayerResult, PropertyScores,
            )
            from agentegrity.core.profile import (
                AgentProfile, AgentType, DeploymentContext, RiskTier,
            )

            profile = AgentProfile(
                name="cross-proc",
                agent_type=AgentType.TOOL_USING,
                capabilities=["tool_use"],
                deployment_context=DeploymentContext.CLOUD,
                risk_tier=RiskTier.MEDIUM,
            )
            score = IntegrityScore(
                composite=0.85,
                properties=PropertyScores(adversarial_coherence=0.9),
                layer_results=[
                    LayerResult(
                        layer_name="adversarial", score=0.9, passed=True,
                        action="pass", details={"matches": 0}, latency_ms=12.3,
                    )
                ],
            )
            rec = build_attestation_record(profile, score)
            print(rec.evidence[0].content_hash)
            """
        )
        out1 = subprocess.check_output(
            [sys.executable, "-c", script], text=True
        ).strip()
        out2 = subprocess.check_output(
            [sys.executable, "-c", script], text=True
        ).strip()
        assert out1 == out2, f"Evidence hash differs across processes: {out1} vs {out2}"
        assert len(out1) == 64

    def test_helper_signs_when_key_supplied(self):
        try:
            from agentegrity.core.attestation import generate_signing_key
        except ImportError:
            pytest.skip("cryptography not installed")

        try:
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        profile = self._stub_profile()
        score = self._stub_score()
        record = build_attestation_record(profile, score, signing_key=key)
        assert record.signature is not None
        assert record.verify() is True

    def test_helper_signs_with_chain_link_baked_in(self):
        """Signed record's signature covers chain_previous, so it
        survives append() without invalidation."""
        try:
            from agentegrity.core.attestation import generate_signing_key
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        profile = self._stub_profile()
        chain = AttestationChain()

        r1 = build_attestation_record(profile, self._stub_score(), signing_key=key)
        chain.append(r1)

        r2 = build_attestation_record(
            profile,
            self._stub_score(layer_score=0.7),
            previous_record_hash=r1.content_hash,
            signing_key=key,
        )
        chain.append(r2)

        assert chain.verify_chain()
        assert r1.verify() is True
        assert r2.verify() is True


class TestAdapterSigningKey:
    """The signing_key param on _BaseAdapter signs every attestation
    record produced by _run_evaluation.
    """

    def test_adapter_without_signing_key_leaves_records_unsigned(self):
        from agentegrity.adapters.base import _BaseAdapter
        from agentegrity.core.profile import (
            AgentProfile,
            AgentType,
            DeploymentContext,
            RiskTier,
        )

        adapter = _BaseAdapter(
            profile=AgentProfile(
                name="unsigned",
                agent_type=AgentType.TOOL_USING,
                capabilities=["tool_use"],
                deployment_context=DeploymentContext.CLOUD,
                risk_tier=RiskTier.MEDIUM,
            ),
        )
        adapter._run_evaluation({"input": "hi"})
        assert len(adapter.attestation_chain.records) == 1
        assert adapter.attestation_chain.records[0].signature is None

    def test_adapter_with_signing_key_signs_records(self):
        try:
            from agentegrity.core.attestation import generate_signing_key
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        from agentegrity.adapters.base import _BaseAdapter
        from agentegrity.core.profile import (
            AgentProfile,
            AgentType,
            DeploymentContext,
            RiskTier,
        )

        adapter = _BaseAdapter(
            profile=AgentProfile(
                name="signed",
                agent_type=AgentType.TOOL_USING,
                capabilities=["tool_use"],
                deployment_context=DeploymentContext.CLOUD,
                risk_tier=RiskTier.MEDIUM,
            ),
            signing_key=key,
        )
        adapter._run_evaluation({"input": "hi"})
        adapter._run_evaluation({"input": "again"})

        records = adapter.attestation_chain.records
        assert len(records) == 2
        for r in records:
            assert r.signature is not None
            assert r.verify() is True
        assert adapter.attestation_chain.verify_chain()
