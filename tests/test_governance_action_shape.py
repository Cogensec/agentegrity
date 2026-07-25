"""Untrusted tool arguments must never shadow the trusted action fields.

The tool_calls buffer entry becomes ``context["action"]``, which the
governance rules match on. When agent-supplied arguments were spread
flat into that dict, an argument named "tool" overwrote the real tool
name and GOV-001's sensitive-tool gate evaluated the forgery: a
prompt-injected agent calling payment_execute with {"tool": "noop"}
skipped approval entirely, and the attestation recorded a pass for a
check that never ran. The same trick overrode "type".

Arguments now live under action["arguments"], so no agent-controlled
key can collide with "tool"/"type".
"""

from __future__ import annotations

import asyncio

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers.governance import GovernanceLayer


def _profile(risk_tier: RiskTier = RiskTier.HIGH) -> AgentProfile:
    return AgentProfile(
        name="action-shape-test",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=risk_tier,
    )


def _adapter(**kwargs) -> _BaseAdapter:
    return _BaseAdapter(
        profile=_profile(),
        evaluator=IntegrityEvaluator(layers=[GovernanceLayer()]),
        **kwargs,
    )


def _pre_tool_use(adapter: _BaseAdapter, tool_name: str, tool_input: dict) -> dict:
    return asyncio.new_event_loop().run_until_complete(
        adapter.on_event(
            "pre_tool_use", {"tool_name": tool_name, "tool_input": tool_input}
        )
    )


def _denied(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestArgumentsCannotShadowTrustedFields:
    def test_argument_named_tool_does_not_override_tool_name(self):
        adapter = _adapter()
        _pre_tool_use(adapter, "payment_execute", {"tool": "noop"})
        action = adapter._buffer.to_evaluation_context()["action"]
        assert action["tool"] == "payment_execute"
        assert action["arguments"]["tool"] == "noop"

    def test_argument_named_type_does_not_override_type(self):
        adapter = _adapter()
        _pre_tool_use(adapter, "payment_execute", {"type": "respond"})
        action = adapter._buffer.to_evaluation_context()["action"]
        assert action["type"] == "tool_call"
        assert action["arguments"]["type"] == "respond"

    def test_poisoned_call_still_denied_under_enforcement(self):
        # The bypass, end to end: without the fix GOV-001 sees "noop",
        # never escalates, and the dangerous tool runs.
        adapter = _adapter(enforce=True)
        assert _denied(_pre_tool_use(adapter, "payment_execute", {"tool": "noop"})) is True

    def test_honest_sensitive_call_still_denied(self):
        adapter = _adapter(enforce=True)
        assert _denied(_pre_tool_use(adapter, "payment_execute", {"amount": 5})) is True

    def test_benign_call_still_allowed(self):
        adapter = _adapter(enforce=True)
        assert _denied(_pre_tool_use(adapter, "search", {"tool": "noop"})) is False


class TestGovernanceRulesReadNestedArguments:
    def test_gov_001_matches_trusted_tool_name(self):
        layer = GovernanceLayer(policy_set="enterprise-default")
        result = layer.evaluate(
            _profile(),
            {
                "action": {
                    "tool": "payment_execute",
                    "type": "tool_call",
                    "arguments": {"tool": "noop", "type": "respond"},
                }
            },
        )
        assert result.action == "escalate"

    def test_gov_003_reads_amount_from_arguments(self):
        layer = GovernanceLayer(policy_set="enterprise-default")
        result = layer.evaluate(
            _profile(RiskTier.LOW),
            {"action": {"type": "financial", "arguments": {"amount": 5000}}},
        )
        assert result.action == "escalate"

    def test_gov_003_below_threshold_passes(self):
        layer = GovernanceLayer(policy_set="enterprise-default")
        result = layer.evaluate(
            _profile(RiskTier.LOW),
            {"action": {"type": "financial", "arguments": {"amount": 10}}},
        )
        assert result.action == "pass"

    def test_gov_003_ignores_top_level_amount(self):
        # A flat "amount" is no longer honored — it could only have come
        # from untrusted arguments under the old shape.
        layer = GovernanceLayer(policy_set="enterprise-default")
        result = layer.evaluate(
            _profile(RiskTier.LOW),
            {"action": {"type": "financial", "amount": 5000}},
        )
        assert result.action == "pass"

    def test_action_without_arguments_key_is_safe(self):
        layer = GovernanceLayer(policy_set="enterprise-default")
        result = layer.evaluate(_profile(RiskTier.LOW), {"action": {"type": "respond"}})
        assert result.action == "pass"
