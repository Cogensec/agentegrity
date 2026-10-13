"""Tests for the Recovery Layer."""

import asyncio

from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.core.attestation import AttestationChain, AttestationRecord
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier
from agentegrity.layers.checkpoint import InMemoryCheckpoint
from agentegrity.layers.recovery import RecoveryLayer


def _make_profile(**kwargs):
    defaults = {
        "name": "test-agent",
        "agent_type": AgentType.TOOL_USING,
        "capabilities": ["tool_use"],
        "deployment_context": DeploymentContext.CLOUD,
        "risk_tier": RiskTier.MEDIUM,
    }
    defaults.update(kwargs)
    return AgentProfile(**defaults)


class TestRecoveryLayer:
    def test_clean_evaluation_no_context(self):
        layer = RecoveryLayer()
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert result.layer_name == "recovery"
        assert 0.0 <= result.score <= 1.0
        assert result.action in ("pass", "alert", "escalate", "block")

    def test_recovery_capable_agent_scores_higher(self):
        # Capability means an attached implementation, not a declared string.
        profile = _make_profile(capabilities=["tool_use"])
        basic_result = RecoveryLayer().evaluate(profile)
        recovery_result = RecoveryLayer(checkpoint=InMemoryCheckpoint()).evaluate(profile)
        assert recovery_result.score > basic_result.score

    def test_an_adapter_binds_its_chain_so_tampering_escalates(self):
        adapter = ClaudeAdapter(profile=_make_profile())
        (recovery,) = [layer for layer in adapter._evaluator.layers
                       if isinstance(layer, RecoveryLayer)]
        assert recovery._chain is adapter.attestation_chain
        asyncio.run(adapter.on_event("user_prompt_submit", {"prompt": "hi"}))
        adapter.attestation_chain._records[0].chain_previous = "tampered_hash"
        asyncio.run(adapter.on_event("user_prompt_submit", {"prompt": "again"}))
        result = next(r for r in adapter.events[-1].evaluation_result.layer_results
                      if r.layer_name == "recovery")
        assert result.details["chain_intact"] is False
        assert result.action == "escalate"

    def test_sustained_degradation_detected(self):
        # Scores that drop significantly
        history = [0.95, 0.93, 0.90, 0.88, 0.70, 0.65, 0.60, 0.55]
        layer = RecoveryLayer(score_history=history, degradation_threshold=0.15)
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert result.details["sustained_degradation"] is True
        assert result.action == "alert"

    def test_no_degradation_with_stable_scores(self):
        history = [0.90, 0.91, 0.89, 0.90, 0.92, 0.91, 0.90, 0.91]
        layer = RecoveryLayer(score_history=history)
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert result.details["sustained_degradation"] is False

    def test_chain_intact(self):
        chain = AttestationChain()
        record = AttestationRecord(
            agent_id="test-agent",
            integrity_score={"composite": 0.95},
            layer_states={},
        )
        chain.append(record)
        layer = RecoveryLayer(chain=chain)
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert result.details["chain_intact"] is True
        assert result.details["chain_length"] == 1

    def test_chain_tampered_escalates(self):
        chain = AttestationChain()
        record = AttestationRecord(
            agent_id="test-agent",
            integrity_score={"composite": 0.95},
            layer_states={},
        )
        chain.append(record)
        # Tamper with the chain by modifying the link hash
        record2 = AttestationRecord(
            agent_id="test-agent",
            integrity_score={"composite": 0.90},
            layer_states={},
        )
        chain.append(record2)
        chain._records[1].chain_previous = "tampered_hash"
        layer = RecoveryLayer(chain=chain)
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert result.details["chain_intact"] is False
        assert result.action == "escalate"

    def test_baseline_context_improves_score(self):
        layer = RecoveryLayer()
        profile = _make_profile()

        result_no_baseline = layer.evaluate(profile, context={})
        result_with_baseline = layer.evaluate(
            profile,
            context={
                "behavioral_baseline": {
                    "sample_count": 10,
                    "created_at": "2026-04-10T00:00:00+00:00",
                }
            },
        )
        assert result_with_baseline.score >= result_no_baseline.score

    def test_record_score_appends(self):
        layer = RecoveryLayer()
        layer.record_score(0.95)
        layer.record_score(0.90)
        assert len(layer._score_history) == 2

    def test_recovery_score_in_details(self):
        layer = RecoveryLayer()
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert "recovery_score" in result.details

    def test_short_history_no_degradation(self):
        layer = RecoveryLayer(score_history=[0.5, 0.4])
        profile = _make_profile()
        result = layer.evaluate(profile)
        assert result.details["sustained_degradation"] is False
