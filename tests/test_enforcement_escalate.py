"""Enforcement must gate on 'escalate', not only 'block'.

Audit finding H2: built-in governance policies (and cortical drift /
recovery chain-tamper) emit 'escalate', but enforcement previously acted
only on 'block', so "require approval" was silently advisory under
enforce=True. These tests pin the fail-closed behavior and the
approval_handler override across both the adapter and the monitor guard.
"""

from __future__ import annotations

import asyncio

import pytest

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.evaluator import IntegrityEvaluator, LayerResult
from agentegrity.core.monitor import IntegrityMonitor, IntegrityViolationError
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)


class _EscalateLayer:
    """Minimal Layer stub whose verdict is always 'escalate'."""

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
        name="escalate-test",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.HIGH,
    )


def _evaluator() -> IntegrityEvaluator:
    return IntegrityEvaluator(layers=[_EscalateLayer()])


def _pre_tool_use(adapter: _BaseAdapter) -> dict:
    return asyncio.new_event_loop().run_until_complete(
        adapter.on_event("pre_tool_use", {"tool_name": "payment_execute", "tool_input": {}})
    )


def _denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestAdapterEscalateEnforcement:
    def test_escalate_denies_without_handler(self):
        adapter = _BaseAdapter(profile=_profile(), evaluator=_evaluator(), enforce=True)
        assert _denied(_pre_tool_use(adapter)) is True

    def test_escalate_allowed_when_handler_approves(self):
        adapter = _BaseAdapter(
            profile=_profile(),
            evaluator=_evaluator(),
            enforce=True,
            approval_handler=lambda profile, score, action: True,
        )
        assert _denied(_pre_tool_use(adapter)) is False

    def test_escalate_denied_when_handler_rejects(self):
        adapter = _BaseAdapter(
            profile=_profile(),
            evaluator=_evaluator(),
            enforce=True,
            approval_handler=lambda profile, score, action: False,
        )
        assert _denied(_pre_tool_use(adapter)) is True

    def test_raising_handler_fails_closed(self):
        def boom(profile, score, action):
            raise RuntimeError("approval service down")

        adapter = _BaseAdapter(
            profile=_profile(),
            evaluator=_evaluator(),
            enforce=True,
            approval_handler=boom,
        )
        assert _denied(_pre_tool_use(adapter)) is True

    def test_no_enforce_never_denies(self):
        adapter = _BaseAdapter(profile=_profile(), evaluator=_evaluator(), enforce=False)
        assert _denied(_pre_tool_use(adapter)) is False

    def test_handler_receives_candidate_action(self):
        seen = {}

        def handler(profile, score, action):
            seen.update(action)
            return True

        adapter = _BaseAdapter(
            profile=_profile(),
            evaluator=_evaluator(),
            enforce=True,
            approval_handler=handler,
        )
        _pre_tool_use(adapter)
        assert seen["tool_name"] == "payment_execute"
        assert seen["type"] == "tool_call"


class TestMonitorGuardEscalateEnforcement:
    def _run(self, monitor):
        @monitor.guard
        def action(context=None):
            return "ran"

        return action(context={})

    def test_guard_blocks_escalate_without_handler(self):
        monitor = IntegrityMonitor(profile=_profile(), evaluator=_evaluator())
        with pytest.raises(IntegrityViolationError):
            self._run(monitor)

    def test_guard_allows_escalate_when_approved(self):
        monitor = IntegrityMonitor(
            profile=_profile(),
            evaluator=_evaluator(),
            approval_handler=lambda profile, score, context: True,
        )
        assert self._run(monitor) == "ran"
