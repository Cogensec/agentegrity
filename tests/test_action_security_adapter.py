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


def _setup(run_id: str | None = None) -> tuple[AgentProfile, ActionSecurityGate]:
    profile = AgentProfile.default(name="synthetic-banking")
    mandate = TaskMandate(
        mandate_id="m-1", principal_id="customer-1", agent_id=profile.agent_id,
        run_id=run_id,
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
    # A pre-tool hook alone must not report an execution that may never occur.
    assert gate.receipt_for(event.data["action_authority"]["action_id"]) is None
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

@pytest.mark.asyncio
async def test_run_bound_mandate_is_used_as_adapter_session_id():
    profile, gate = _setup(run_id="bank-run-001")
    adapter = ClaudeAdapter(
        profile=profile,
        evaluator=IntegrityEvaluator(layers=[_PassLayer()]),
        enforce=True, action_security=gate, stream_from_env=False,
    )
    verdict = await adapter.on_event(
        "pre_tool_use", {"tool_name": "read_account", "tool_input": {"account_id": "account-42"}}
    )
    assert verdict == {}
    record = adapter.events[-1].data["action_authority"]
    assert record["run_id"] == "bank-run-001"
    assert record["allowed"] is True
    assert gate.receipt_for(record["action_id"]) is None


@pytest.mark.asyncio
async def test_explicit_matching_session_id_with_bound_mandate():
    profile, gate = _setup(run_id="bank-run-002")
    adapter = ClaudeAdapter(
        profile=profile,
        evaluator=IntegrityEvaluator(layers=[_PassLayer()]),
        enforce=True, action_security=gate, stream_from_env=False,
        session_id="bank-run-002",
    )
    verdict = await adapter.on_event(
        "pre_tool_use", {"tool_name": "read_account", "tool_input": {"account_id": "account-42"}}
    )
    assert verdict == {}
    assert adapter.events[-1].data["action_authority"]["run_id"] == "bank-run-002"


def test_explicit_session_id_mismatch_rejected_at_construction():
    profile, gate = _setup(run_id="bank-run-001")
    with pytest.raises(ValueError, match="does not match"):
        ClaudeAdapter(
            profile=profile, enforce=True, action_security=gate,
            stream_from_env=False, session_id="untrusted-run",
        )


def test_empty_session_id_rejected():
    profile, gate = _setup()
    with pytest.raises(ValueError, match="nonempty"):
        ClaudeAdapter(
            profile=profile, enforce=True, action_security=gate,
            stream_from_env=False, session_id="",
        )


class _BlockLayer:
    name = "blocking_test_layer"

    def evaluate(self, profile, context=None):
        return LayerResult(layer_name=self.name, score=0.0, passed=False, action="block")


@pytest.mark.asyncio
async def test_governance_block_does_not_create_phantom_pending_receipt():
    profile, gate = _setup(run_id="bank-run-003")
    adapter = ClaudeAdapter(
        profile=profile,
        evaluator=IntegrityEvaluator(layers=[_BlockLayer()]),
        enforce=True, action_security=gate, stream_from_env=False,
    )
    verdict = await adapter.on_event(
        "pre_tool_use", {"tool_name": "read_account", "tool_input": {"account_id": "account-42"}}
    )
    assert verdict["hookSpecificOutput"]["permissionDecision"] == "deny"
    authority = adapter.events[-1].data["action_authority"]
    assert authority["allowed"] is True
    assert gate.receipt_for(authority["action_id"]) is None
