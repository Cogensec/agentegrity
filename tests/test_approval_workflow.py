"""First-class HITL approval workflow.

ApprovalWorkflow wraps any approval handler with a timeout policy
(fail-closed by default) and returns a rich ApprovalDecision. Under
enforcement, the adapter records every consulted approval as a
DecisionRecord on the chain, so "who approved this and when" is part
of the verifiable provenance.
"""

from __future__ import annotations

import asyncio
import time

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.approval import ApprovalDecision, ApprovalWorkflow
from agentegrity.core.evaluator import IntegrityEvaluator, LayerResult
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)


class _EscalateLayer:
    name = "stub_escalate"

    def evaluate(self, profile, context=None) -> LayerResult:
        return LayerResult(
            layer_name=self.name,
            score=0.5,
            passed=False,
            action="escalate",
            details={},
        )


def _profile() -> AgentProfile:
    return AgentProfile(
        name="approval-test",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.HIGH,
    )


def _adapter(handler) -> _BaseAdapter:
    return _BaseAdapter(
        profile=_profile(),
        evaluator=IntegrityEvaluator(layers=[_EscalateLayer()]),
        enforce=True,
        approval_handler=handler,
    )


def _pre_tool_use(adapter: _BaseAdapter) -> dict:
    return asyncio.new_event_loop().run_until_complete(
        adapter.on_event(
            "pre_tool_use", {"tool_name": "payment_execute", "tool_input": {}}
        )
    )


def _denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestApprovalWorkflow:
    def test_approving_handler_yields_rich_decision(self):
        workflow = ApprovalWorkflow(
            lambda profile, score, action: True, approver="tarique"
        )
        decision = workflow(_profile(), None, {"type": "tool_call"})
        assert isinstance(decision, ApprovalDecision)
        assert decision.approved is True
        assert decision.approver == "tarique"
        assert decision.timed_out is False
        assert decision.resolved_at >= decision.requested_at

    def test_handler_returning_decision_passes_through(self):
        inner = ApprovalDecision(
            approved=False, approver="secops", reason="out of policy"
        )
        workflow = ApprovalWorkflow(lambda p, s, a: inner)
        decision = workflow(_profile(), None, {})
        assert decision.approved is False
        assert decision.approver == "secops"
        assert decision.reason == "out of policy"

    def test_timeout_fails_closed_by_default(self):
        def slow(profile, score, action):
            time.sleep(0.5)
            return True

        workflow = ApprovalWorkflow(slow, timeout=0.05)
        decision = workflow(_profile(), None, {})
        assert decision.approved is False
        assert decision.timed_out is True

    def test_timeout_action_allow_is_explicit_fail_open(self):
        def slow(profile, score, action):
            time.sleep(0.5)
            return False

        workflow = ApprovalWorkflow(slow, timeout=0.05, timeout_action="allow")
        decision = workflow(_profile(), None, {})
        assert decision.approved is True
        assert decision.timed_out is True

    def test_raising_handler_fails_closed(self):
        def boom(profile, score, action):
            raise RuntimeError("approval service down")

        workflow = ApprovalWorkflow(boom, timeout=1.0)
        decision = workflow(_profile(), None, {})
        assert decision.approved is False
        assert decision.timed_out is False
        assert "RuntimeError" in (decision.reason or "")


class TestAdapterRecordsApprovals:
    def test_approval_recorded_on_chain(self):
        adapter = _adapter(
            ApprovalWorkflow(lambda p, s, a: True, approver="tarique")
        )
        result = _pre_tool_use(adapter)
        assert _denied(result) is False
        approvals = [
            r
            for r in adapter.attestation_chain.records
            if r.record_kind == "decision"
            and r.decision_point == "approval"
        ]
        assert len(approvals) == 1
        steps = approvals[0].reasoning_chain
        assert "outcome:approved" in steps
        assert "approver:tarique" in steps
        assert adapter.attestation_chain.verify_chain()

    def test_denial_recorded_on_chain(self):
        adapter = _adapter(
            ApprovalWorkflow(lambda p, s, a: False, approver="secops")
        )
        result = _pre_tool_use(adapter)
        assert _denied(result) is True
        approvals = [
            r
            for r in adapter.attestation_chain.records
            if r.record_kind == "decision"
            and r.decision_point == "approval"
        ]
        assert len(approvals) == 1
        assert "outcome:denied" in approvals[0].reasoning_chain

    def test_timeout_recorded(self):
        def slow(profile, score, action):
            time.sleep(0.5)
            return True

        adapter = _adapter(ApprovalWorkflow(slow, timeout=0.05))
        result = _pre_tool_use(adapter)
        assert _denied(result) is True
        approvals = [
            r
            for r in adapter.attestation_chain.records
            if r.record_kind == "decision"
            and r.decision_point == "approval"
        ]
        assert "timed_out:true" in approvals[0].reasoning_chain

    def test_plain_bool_handler_also_recorded(self):
        # Back-compat handlers still produce provenance, with a
        # generic approver identity.
        adapter = _adapter(lambda profile, score, action: True)
        _pre_tool_use(adapter)
        approvals = [
            r
            for r in adapter.attestation_chain.records
            if r.record_kind == "decision"
            and r.decision_point == "approval"
        ]
        assert len(approvals) == 1
        assert "approver:approval_handler" in approvals[0].reasoning_chain

    def test_no_handler_no_approval_record(self):
        adapter = _BaseAdapter(
            profile=_profile(),
            evaluator=IntegrityEvaluator(layers=[_EscalateLayer()]),
            enforce=True,
        )
        result = _pre_tool_use(adapter)
        assert _denied(result) is True
        approvals = [
            r
            for r in adapter.attestation_chain.records
            if r.record_kind == "decision"
            and r.decision_point == "approval"
        ]
        assert approvals == []
