"""Adapter integration: a configured mandate participates in pre-tool decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.core.action_security import (
    ActionSecurityGate,
    EffectType,
    TaskMandate,
    ToolAuthority,
)
from agentegrity.core.evaluator import IntegrityEvaluator, LayerResult
from agentegrity.core.profile import AgentProfile


class _PassLayer:
    name = "passing_test_layer"

    def evaluate(self, profile, context=None):
        return LayerResult(layer_name=self.name, score=1.0, passed=True, action="pass")


def _setup() -> tuple[AgentProfile, ActionSecurityGate]:
    profile = AgentProfile.default(name="synthetic-banking")
    mandate = TaskMandate(
        mandate_id="m-1", principal_id="customer-1", agent_id=profile.agent_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        allowed_tools=frozenset({"read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ}),
    )
    return profile, ActionSecurityGate(
        mandate, {"read_account": ToolAuthority(EffectType.READ, resource_argument="account_id")}
    )


@pytest.mark.asyncio
async def test_authorized_call_goes_through_existing_hook_and_records_provenance():
    profile, gate = _setup()
    adapter = ClaudeAdapter(profile=profile,
                            evaluator=IntegrityEvaluator(layers=[_PassLayer()]),
                            enforce=True, action_security=gate, stream_from_env=False)
    verdict = await adapter.on_event(
        "pre_tool_use", {"tool_name": "read_account", "tool_input": {"account_id": "account-42"}}
    )
    assert verdict == {}
    event = adapter.events[-1]
    assert event.data["action_authority"]["allowed"] is True
    assert gate.receipt_for(event.data["action_authority"]["action_id"]).assurance == "unverified"
    decisions = [r for r in adapter.attestation_chain.records
                 if r.record_kind == "decision" and r.decision_point == "pre_tool_use"]
    assert len(decisions) == 1
    assert decisions[0].candidate_action["action_authority"]["reason"] == "allowed"


@pytest.mark.asyncio
async def test_unauthorized_call_denies_with_separate_authority_verdict():
    profile, gate = _setup()
    adapter = ClaudeAdapter(profile=profile,
                            evaluator=IntegrityEvaluator(layers=[_PassLayer()]),
                            enforce=True, action_security=gate, stream_from_env=False)
    verdict = await adapter.on_event(
        "pre_tool_use",
        {"tool_name": "prepare_transfer", "tool_input": {"account_id": "account-42"}},
    )
    assert verdict["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "action_authority" in verdict["hookSpecificOutput"]["permissionDecisionReason"]
    assert adapter.events[-1].data["action_authority"]["reason"] == "tool_not_authorized"


def test_action_security_never_runs_advisory_only():
    profile, gate = _setup()
    with pytest.raises(ValueError, match="requires enforce=True"):
        ClaudeAdapter(profile=profile, enforce=False, action_security=gate)
