"""Tests for Phase 4: attestation → decision Evidence linking.

When an :class:`AttestationRecord` is built after one or more
:class:`DecisionRecord`\\s have been appended, each decision contributes
an ``Evidence(evidence_type="decision", source=record_id,
content_hash=...)`` entry. ``AttestationChain.verify_decision_links()``
walks the chain and confirms each link still points at an unaltered
decision."""

import asyncio

import pytest

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.attestation import (
    AttestationChain,
    AttestationRecord,
    build_attestation_record,
)
from agentegrity.core.decision import (
    DecisionRecord,
    build_decision_record,
)
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)


def _profile():
    return AgentProfile(
        name="phase4",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _score():
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
                layer_name="adversarial", score=0.9, passed=True,
                action="pass", details={}, latency_ms=1.0,
            )
        ],
    )


def _adapter():
    return _BaseAdapter(profile=_profile())


class TestAttestationCarriesDecisionEvidence:
    def test_attestation_after_two_decisions_has_two_decision_evidence(self):
        chain = AttestationChain()
        d1 = build_decision_record(
            agent_id="x", decision_point="pre_tool_use",
            candidate_action={"type": "tool_call", "tool_name": "a"},
        )
        chain.append(d1)
        d2 = build_decision_record(
            agent_id="x", decision_point="stop",
            candidate_action={"type": "final_output", "summary": "done"},
            previous_record_hash=d1.content_hash,
        )
        chain.append(d2)
        attest = build_attestation_record(
            _profile(), _score(),
            previous_record_hash=d2.content_hash,
            recent_decisions=[d1, d2],
        )
        chain.append(attest)

        decision_evidence = [
            e for e in attest.evidence if e.evidence_type == "decision"
        ]
        assert len(decision_evidence) == 2
        assert decision_evidence[0].source == d1.record_id
        assert decision_evidence[0].content_hash == d1.content_hash
        assert decision_evidence[1].source == d2.record_id
        assert decision_evidence[1].content_hash == d2.content_hash

    def test_first_attestation_with_no_preceding_decisions_has_no_link(self):
        attest = build_attestation_record(_profile(), _score())
        decision_evidence = [
            e for e in attest.evidence if e.evidence_type == "decision"
        ]
        assert decision_evidence == []

    def test_run_evaluation_links_recent_decisions_only(self):
        """In the adapter flow: pre_tool_use → attestation N → decision M.
        Next pre_tool_use → attestation N+1 must link decision M, then
        produces decision M+1. Subsequent attestation links M+1 only."""
        adapter = _adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "a", "tool_input": {}}
        ))
        # Chain state: [attest1, decision1]
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "b", "tool_input": {}}
        ))
        # Chain state: [attest1, decision1, attest2(links decision1), decision2]
        records = adapter.attestation_chain.records
        attestations = [r for r in records if isinstance(r, AttestationRecord)]
        assert len(attestations) == 2
        # attest1 had no preceding decisions
        a1_decision_evidence = [
            e for e in attestations[0].evidence if e.evidence_type == "decision"
        ]
        assert a1_decision_evidence == []
        # attest2 links the one decision that came between
        a2_decision_evidence = [
            e for e in attestations[1].evidence if e.evidence_type == "decision"
        ]
        decisions = [r for r in records if isinstance(r, DecisionRecord)]
        assert len(a2_decision_evidence) == 1
        assert a2_decision_evidence[0].source == decisions[0].record_id


class TestVerifyDecisionLinks:
    def test_intact_chain_passes_verification(self):
        adapter = _adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "a", "tool_input": {}}
        ))
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "b", "tool_input": {}}
        ))
        assert adapter.attestation_chain.verify_decision_links() is True

    def test_tampering_a_linked_decision_invalidates_links(self):
        adapter = _adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "a", "tool_input": {}}
        ))
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "b", "tool_input": {}}
        ))
        chain = adapter.attestation_chain
        # Tamper the first decision after the linking attestation committed
        decisions = [r for r in chain.records if isinstance(r, DecisionRecord)]
        decisions[0].candidate_action["tool_name"] = "evil"
        # content_hash recomputes from canonical_payload → no longer matches
        # the Evidence content_hash committed by the subsequent attestation
        assert chain.verify_decision_links() is False

    def test_orphan_decision_reference_fails(self):
        """Manually craft an attestation that references a non-existent decision."""
        from agentegrity.core.attestation import Evidence

        chain = AttestationChain()
        attest = build_attestation_record(_profile(), _score())
        attest.evidence.append(Evidence(
            evidence_type="decision",
            source="nonexistent-decision-id",
            content_hash="deadbeef" * 8,
            summary="phantom",
        ))
        chain.append(attest)
        assert chain.verify_decision_links() is False

    def test_decision_after_attestation_fails_link(self):
        """A decision that sits AFTER its linking attestation in the
        chain breaks the temporal ordering and fails verification."""
        from agentegrity.core.attestation import Evidence

        chain = AttestationChain()
        # Build a decision but don't append it yet
        d = build_decision_record(
            agent_id="x", decision_point="pre_tool_use",
            candidate_action={"type": "tool_call", "tool_name": "x"},
        )
        # Attestation references it first (wrong order)
        attest = build_attestation_record(_profile(), _score())
        attest.evidence.append(Evidence(
            evidence_type="decision",
            source=d.record_id,
            content_hash=d.content_hash,
            summary="early",
        ))
        chain.append(attest)
        d.chain_previous = attest.content_hash
        chain.append(d)

        assert chain.verify_decision_links() is False


class TestSignedChainSurvivesLinkVerification:
    def test_signed_linked_chain_passes_both_checks(self):
        try:
            from agentegrity.core.attestation import generate_signing_key
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        adapter = _BaseAdapter(profile=_profile(), signing_key=key)
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "a", "tool_input": {}}
        ))
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "b", "tool_input": {}}
        ))
        chain = adapter.attestation_chain
        assert chain.verify_chain() is True
        assert chain.verify_decision_links() is True
        for r in chain.records:
            assert r.verify() is True
