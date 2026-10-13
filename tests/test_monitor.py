"""Tests for IntegrityMonitor."""

import pytest

from agentegrity.core.approval import ApprovalDecision, ApprovalWorkflow
from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.monitor import (
    IntegrityMonitor,
    IntegrityViolationError,
    ViolationAction,
)
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier
from agentegrity.layers.adversarial import AdversarialLayer
from agentegrity.layers.cortical import CorticalLayer
from agentegrity.layers.governance import GovernanceLayer

from .test_framework_enforcement import _Verdict


def make_profile():
    return AgentProfile(
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def make_evaluator():
    return IntegrityEvaluator(
        layers=[
            AdversarialLayer(coherence_threshold=0.70),
            CorticalLayer(drift_tolerance=0.15),
            GovernanceLayer(policy_set="minimal"),
        ]
    )


class TestIntegrityMonitor:
    def test_basic_evaluation(self):
        monitor = IntegrityMonitor(make_profile(), make_evaluator(), threshold=0.50)
        score = monitor.evaluate()
        assert score.composite > 0
        assert monitor.evaluation_count == 1

    def test_attestation_chain_grows(self):
        monitor = IntegrityMonitor(make_profile(), make_evaluator(), enable_attestation=True)
        monitor.evaluate()
        monitor.evaluate()
        monitor.evaluate()
        assert len(monitor.attestation_chain) == 3
        assert monitor.attestation_chain.verify_chain()

    def test_violation_recorded(self):
        # Use a very high threshold to trigger a violation
        monitor = IntegrityMonitor(
            make_profile(),
            make_evaluator(),
            threshold=0.999,
            on_violation=ViolationAction.ALERT,
        )
        monitor.evaluate()
        assert len(monitor.violations) >= 1

    def test_violation_callback(self):
        events = []
        monitor = IntegrityMonitor(
            make_profile(),
            make_evaluator(),
            threshold=0.999,
            on_violation_callback=lambda e: events.append(e),
        )
        monitor.evaluate()
        assert len(events) >= 1

    def test_guard_decorator_sync(self):
        monitor = IntegrityMonitor(make_profile(), make_evaluator(), threshold=0.30)

        @monitor.guard
        def my_action(context=None):
            return "action_result"

        result = my_action()
        assert result == "action_result"
        assert monitor.evaluation_count == 2  # pre + post

    @pytest.mark.asyncio
    async def test_guard_decorator_async(self):
        monitor = IntegrityMonitor(make_profile(), make_evaluator(), threshold=0.30)

        @monitor.guard
        async def my_async_action(context=None):
            return "async_result"

        result = await my_async_action()
        assert result == "async_result"
        assert monitor.evaluation_count == 2

    def test_guard_blocks_on_low_score(self):
        """If the evaluator produces a block action, guard should raise."""
        from agentegrity.layers.adversarial import ThreatAssessment

        def always_critical(profile, context):
            return [ThreatAssessment(
                channel="test", threat_type="critical",
                severity=0.95, confidence=0.95, description="test"
            )]

        evaluator = IntegrityEvaluator(
            layers=[
                AdversarialLayer(
                    coherence_threshold=0.70,
                    threat_detectors=[always_critical],
                ),
                CorticalLayer(),
                GovernanceLayer(policy_set="minimal"),
            ],
            fail_fast=True,
        )
        monitor = IntegrityMonitor(make_profile(), evaluator, threshold=0.70)

        @monitor.guard
        def blocked_action(context=None):
            return "should not reach here"

        with pytest.raises(IntegrityViolationError):
            blocked_action()


class TestGuardApproval:
    """``guard`` honours what an approval handler decided, whatever it returns."""

    @staticmethod
    def _act(handler):
        monitor = IntegrityMonitor(
            make_profile(),
            IntegrityEvaluator(layers=[_Verdict("escalate")]),
            approval_handler=handler,
        )

        @monitor.guard
        def act(context=None):
            return "ran"

        return act

    def test_a_workflow_denial_blocks(self):
        act = self._act(ApprovalWorkflow(lambda *_: False))
        with pytest.raises(IntegrityViolationError):
            act(context={})

    def test_a_workflow_approval_runs(self):
        assert self._act(ApprovalWorkflow(lambda *_: True))(context={}) == "ran"

    def test_a_returned_denial_decision_blocks(self):
        act = self._act(lambda *_: ApprovalDecision(approved=False))
        with pytest.raises(IntegrityViolationError):
            act(context={})

    def test_a_bool_approval_runs(self):
        assert self._act(lambda *_: True)(context={}) == "ran"
