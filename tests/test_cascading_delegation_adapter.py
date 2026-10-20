"""A subagent hook cannot mint authority; only a trusted parent gate can."""

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


class _Pass:
    name = "synthetic_pass"

    def evaluate(self, profile, context=None):
        return LayerResult(layer_name=self.name, score=1.0, passed=True, action="pass")


def _setup():
    supervisor = AgentProfile.default(name="synthetic-supervisor")
    worker = AgentProfile.default(name="synthetic-worker")
    now = datetime.now(timezone.utc)
    root = ActionSecurityGate(
        TaskMandate(
            mandate_id="parent-mandate", principal_id="synthetic-customer",
            agent_id=supervisor.agent_id, run_id="parent-run",
            expires_at=now + timedelta(minutes=30),
            allowed_tools=frozenset({"read_account", "prepare_transfer"}),
            allowed_resources=frozenset({"account-42"}),
            allowed_effects=frozenset({EffectType.READ, EffectType.FINANCIAL_WRITE}),
        ),
        {
            "read_account": ToolAuthority(
                EffectType.READ, resource_argument="account_id"
            ),
            "prepare_transfer": ToolAuthority(
                EffectType.FINANCIAL_WRITE, resource_argument="account_id"
            ),
        },
        max_delegation_depth=1,
    )
    child = root.delegate(
        agent_id=worker.agent_id, run_id="child-run",
        allowed_tools=frozenset({"read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=now + timedelta(minutes=10),
    )
    return supervisor, worker, root, child


@pytest.mark.asyncio
async def test_child_adapter_enforces_delegated_tool_limit_and_binds_run():
    _, worker, _, child = _setup()
    adapter = ClaudeAdapter(
        profile=worker, evaluator=IntegrityEvaluator(layers=[_Pass()]),
        action_security=child, enforce=True, stream_from_env=False,
    )
    assert adapter.session_id == "child-run"
    allowed = await adapter.on_event("pre_tool_use", {
        "tool_name": "read_account",
        "tool_input": {"account_id": "account-42"},
    })
    assert allowed == {}
    denied = await adapter.on_event("pre_tool_use", {
        "tool_name": "prepare_transfer",
        "tool_input": {"account_id": "account-42"},
    })
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert adapter.events[-1].data["action_authority"]["reason"] == (
        "tool_not_authorized"
    )


@pytest.mark.asyncio
async def test_child_adapter_enforces_parent_revocation_after_installation():
    _, worker, root, child = _setup()
    adapter = ClaudeAdapter(
        profile=worker, evaluator=IntegrityEvaluator(layers=[_Pass()]),
        action_security=child, enforce=True, stream_from_env=False,
    )
    root.revoke()
    denied = await adapter.on_event("pre_tool_use", {
        "tool_name": "read_account",
        "tool_input": {"account_id": "account-42"},
    })
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert adapter.events[-1].data["action_authority"]["reason"] == (
        "delegation_revoked"
    )


@pytest.mark.asyncio
async def test_subagent_start_event_is_only_a_lifecycle_attestation():
    supervisor, _, root, _ = _setup()
    adapter = ClaudeAdapter(
        profile=supervisor, evaluator=IntegrityEvaluator(layers=[_Pass()]),
        action_security=root, enforce=True, stream_from_env=False,
    )
    before = root.delegation_lineage
    result = await adapter.on_event("subagent_start", {
        "agent_id": "untrusted-agent-id",
        "claimed_roles": ["admin", "system"],
        "allowed_tools": ["prepare_transfer"],
    })
    assert result == {}
    assert root.delegation_lineage == before
    assert root.check(
        agent_id="untrusted-agent-id", run_id="parent-run",
        tool_name="prepare_transfer", arguments={"account_id": "account-42"},
    ).reason == "agent_identity_mismatch"
