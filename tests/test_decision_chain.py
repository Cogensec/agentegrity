"""Tests for the heterogeneous AttestationChain holding AttestationRecord
+ DecisionRecord and the JSON serialization round-trip."""


import pytest

from agentegrity.core.attestation import (
    AttestationChain,
    AttestationRecord,
    build_attestation_record,
)
from agentegrity.core.decision import (
    DecisionRecord,
    build_decision_record,
)


def _stub_profile():
    from agentegrity.core.profile import (
        AgentProfile,
        AgentType,
        DeploymentContext,
        RiskTier,
    )
    return AgentProfile(
        name="phase2",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _stub_score(layer_score=0.9):
    from agentegrity.core.evaluator import (
        IntegrityScore,
        LayerResult,
        PropertyScores,
    )
    return IntegrityScore(
        composite=0.85,
        properties=PropertyScores(adversarial_coherence=layer_score),
        layer_results=[
            LayerResult(
                layer_name="adversarial", score=layer_score, passed=True,
                action="pass", details={"matches": 0}, latency_ms=12.3,
            )
        ],
    )


def _make_attestation(prev_hash=None):
    return build_attestation_record(
        _stub_profile(), _stub_score(), previous_record_hash=prev_hash,
    )


def _make_decision(prev_hash=None, decision_point="pre_tool_use"):
    return build_decision_record(
        agent_id="phase2-agent",
        decision_point=decision_point,
        candidate_action={"type": "tool_call", "tool_name": "calc"},
        previous_record_hash=prev_hash,
    )


class TestHeterogeneousChain:
    def test_decision_then_attestation_verifies(self):
        chain = AttestationChain()
        d = _make_decision()
        chain.append(d)
        a = _make_attestation(prev_hash=d.content_hash)
        chain.append(a)
        assert chain.verify_chain()
        ok, broken, kind = chain.verify_chain_detailed()
        assert ok is True
        assert broken is None
        assert kind is None

    def test_three_record_mixed_chain(self):
        chain = AttestationChain()
        d1 = _make_decision()
        chain.append(d1)
        a = _make_attestation(prev_hash=d1.content_hash)
        chain.append(a)
        d2 = _make_decision(prev_hash=a.content_hash, decision_point="stop")
        chain.append(d2)

        assert chain.verify_chain()
        assert len(chain) == 3
        assert chain.records[0].record_kind == "decision"
        assert chain.records[1].record_kind == "attestation"
        assert chain.records[2].record_kind == "decision"

    def test_tampered_middle_record_reported_with_index_and_kind(self):
        chain = AttestationChain()
        chain.append(_make_decision())
        chain.append(_make_attestation(prev_hash=chain.latest.content_hash))
        chain.append(_make_decision(prev_hash=chain.latest.content_hash))

        # Tamper the middle (attestation) record's chain_previous
        chain.records[1].chain_previous = "tampered"
        ok, broken_idx, broken_kind = chain.verify_chain_detailed()
        assert ok is False
        assert broken_idx == 1
        assert broken_kind == "attestation"

    def test_append_mismatched_chain_previous_raises(self):
        chain = AttestationChain()
        chain.append(_make_decision())
        bad = _make_decision(prev_hash="not_a_real_hash")
        with pytest.raises(ValueError, match="chain_previous mismatch"):
            chain.append(bad)


class TestChainJsonRoundTrip:
    def test_to_json_from_json_preserves_record_kinds(self):
        chain = AttestationChain()
        d1 = _make_decision()
        chain.append(d1)
        a = _make_attestation(prev_hash=d1.content_hash)
        chain.append(a)
        d2 = _make_decision(prev_hash=a.content_hash, decision_point="stop")
        chain.append(d2)

        text = chain.to_json()
        rebuilt = AttestationChain.from_json(text)

        assert len(rebuilt) == 3
        assert isinstance(rebuilt.records[0], DecisionRecord)
        assert isinstance(rebuilt.records[1], AttestationRecord)
        assert isinstance(rebuilt.records[2], DecisionRecord)
        assert rebuilt.verify_chain()

    def test_to_records_dict_includes_record_kind(self):
        chain = AttestationChain()
        chain.append(_make_attestation())
        dicts = chain.to_records_dict()
        assert dicts[0]["record_kind"] == "attestation"

    def test_decision_record_in_dict_has_decision_fields(self):
        chain = AttestationChain()
        chain.append(_make_decision())
        d = chain.to_records_dict()[0]
        assert d["record_kind"] == "decision"
        assert d["decision_point"] == "pre_tool_use"
        assert d["capture_tier"] == "minimal"


class TestBackwardCompat:
    """Honest break: old chains (no record_kind field) fail verification
    because the canonical payload now includes record_kind, so the
    recomputed content_hash differs from the stored chain_previous in
    the next record. Loading still works; verification doesn't."""

    def test_old_format_loads_without_record_kind(self):
        """A pre-v0.7 dict with no record_kind field defaults to attestation."""
        old_dict = {
            "record_id": "old-id-1",
            "agent_id": "old-agent",
            "timestamp": "2025-01-01T00:00:00+00:00",
            "integrity_score": {"composite": 0.85},
            "layer_states": {},
            "evidence": [],
            "chain_previous": None,
            "content_hash": "ignored",
            "signature": None,
            "public_key": None,
        }
        chain = AttestationChain.from_dict_list([old_dict])
        assert len(chain) == 1
        assert isinstance(chain.records[0], AttestationRecord)
        assert chain.records[0].record_kind == "attestation"

    def test_unknown_record_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown record_kind"):
            AttestationChain.from_dict_list([{"record_kind": "bogus"}])


class TestSignedChainSurvivesRoundTrip:
    def test_signed_heterogeneous_chain_verifies_after_json_round_trip(self):
        try:
            from agentegrity.core.attestation import generate_signing_key
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        chain = AttestationChain()
        d = build_decision_record(
            agent_id="signed",
            decision_point="pre_tool_use",
            candidate_action={"type": "tool_call", "tool_name": "calc"},
            signing_key=key,
        )
        chain.append(d)
        a = build_attestation_record(
            _stub_profile(),
            _stub_score(),
            previous_record_hash=d.content_hash,
            signing_key=key,
        )
        chain.append(a)

        text = chain.to_json()
        rebuilt = AttestationChain.from_json(text)
        assert rebuilt.verify_chain()
        for r in rebuilt.records:
            assert r.verify() is True
