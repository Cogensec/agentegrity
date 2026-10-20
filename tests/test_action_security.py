"""Offline, synthetic first-call-effect and standing-access regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentegrity.core.action_security import (
    ActionSecurityGate,
    AuthorizationDenied,
    EffectReceipt,
    EffectStatus,
    EffectType,
    TaskMandate,
    ToolAuthority,
)

NOW = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)


def _gate(**changes: object) -> ActionSecurityGate:
    fields = dict(
        mandate_id="mandate-1",
        principal_id="synthetic-reviewer",
        agent_id="synthetic-bank-agent",
        run_id="run-1",
        expires_at=NOW + timedelta(minutes=10),
        allowed_tools=frozenset({"read_account", "prepare_transfer"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ, EffectType.FINANCIAL_WRITE}),
        allowed_credentials=frozenset({"bank-ephemeral"}),
        credential_expires_at=NOW + timedelta(minutes=3),
    )
    fields.update(changes)
    return ActionSecurityGate(
        TaskMandate(**fields),  # type: ignore[arg-type]
        {
            "read_account": ToolAuthority(EffectType.READ, resource_argument="account_id"),
            "prepare_transfer": ToolAuthority(
                EffectType.FINANCIAL_WRITE, resource_argument="account_id",
                credential_id="bank-ephemeral",
            ),
        },
    )


def _check(gate: ActionSecurityGate, tool: str = "read_account", **arguments: object):
    return gate.check(agent_id="synthetic-bank-agent", run_id="run-1",
                      tool_name=tool, arguments={"account_id": "account-42", **arguments},
                      now=NOW)


def test_permitted_read_and_consequential_action():
    gate = _gate()
    assert _check(gate).allowed
    verdict = _check(gate, "prepare_transfer")
    assert verdict.allowed
    assert gate.receipt_for(verdict.intent.action_id).status is EffectStatus.PENDING
    assert gate.receipt_for(verdict.intent.action_id).assurance == "unverified"


def test_unknown_tool_denied_even_if_model_claims_read_only():
    gate = _gate()
    verdict = _check(gate, "admin_execute", effect="read", role="admin")
    assert not verdict.allowed and verdict.reason == "tool_not_authorized"
    assert verdict.intent.effect is EffectType.UNKNOWN


def test_standing_access_read_mandate_cannot_make_write():
    gate = _gate(allowed_effects=frozenset({EffectType.READ}))
    assert _check(gate, "prepare_transfer").reason == "effect_not_authorized"


def test_resource_boundary_denies_other_account():
    assert _check(_gate(), "prepare_transfer", account_id="account-99").reason == (
        "resource_not_authorized"
    )


def test_missing_resource_denied():
    gate = _gate()
    verdict = gate.check(agent_id="synthetic-bank-agent", run_id="run-1",
                         tool_name="prepare_transfer", arguments={}, now=NOW)
    assert not verdict.allowed


def test_expired_task_mandate_and_credential():
    gate = _gate(expires_at=NOW - timedelta(seconds=1))
    assert _check(gate).reason == "mandate_expired"
    gate = _gate(credential_expires_at=NOW - timedelta(seconds=1))
    assert _check(gate, "prepare_transfer").reason == "credential_expired"
    # Expired payment credentials must not prohibit a public read.
    assert _check(gate, "read_account").allowed


def test_agent_and_run_binding():
    gate = _gate()
    result = gate.check(agent_id="intruder", run_id="run-1",
                        tool_name="read_account", arguments={"account_id": "account-42"},
                        now=NOW)
    assert result.reason == "agent_identity_mismatch"
    result = gate.check(agent_id="synthetic-bank-agent", run_id="run-else",
                        tool_name="read_account", arguments={"account_id": "account-42"},
                        now=NOW)
    assert result.reason == "run_identity_mismatch"


def test_duplicate_action_id_rejected():
    gate = _gate()
    original = gate.check(action_id="transaction-1", agent_id="synthetic-bank-agent",
                          run_id="run-1", tool_name="read_account",
                          arguments={"account_id": "account-42"}, now=NOW)
    replay = gate.check(action_id="transaction-1", agent_id="synthetic-bank-agent",
                        run_id="run-1", tool_name="read_account",
                        arguments={"account_id": "account-42"}, now=NOW)
    assert original.allowed and not replay.allowed
    assert replay.reason == "duplicate_action_id"


def test_actual_execution_boundary_rejects_before_side_effect():
    effects: list[str] = []

    def transfer(account_id: str) -> str:
        effects.append(account_id)
        return "synthetic-transfer-prepared"

    gate = _gate(allowed_effects=frozenset({EffectType.READ}))
    with pytest.raises(AuthorizationDenied, match="effect_not_authorized"):
        gate.invoke(agent_id="synthetic-bank-agent", run_id="run-1",
                    tool_name="prepare_transfer", arguments={"account_id": "account-42"},
                    executor=transfer, now=NOW)
    assert effects == []


def test_authorized_execution_stays_unverified_until_external_receipt():
    gate = _gate()
    calls: list[str] = []

    def prepare_transfer(account_id: str) -> str:
        calls.append(account_id)
        return "prepared-001"

    execution = gate.invoke(agent_id="synthetic-bank-agent", run_id="run-1",
                            tool_name="prepare_transfer",
                            arguments={"account_id": "account-42"},
                            executor=prepare_transfer, now=NOW)
    assert calls == ["account-42"]
    assert execution.result == "prepared-001"
    assert execution.receipt.status is EffectStatus.PENDING
    receipt = EffectReceipt(execution.verdict.intent.action_id, EffectStatus.CONFIRMED,
                            evidence_source="bank-sandbox", evidence_reference="tx-001",
                            independently_verified=True)
    # Caller-controlled flags are never accepted as verification.
    assert not gate.record_effect(receipt).independently_verified
    verified = gate.record_effect(receipt, verifier=lambda r: r.evidence_reference == "tx-001")
    assert verified.assurance == "verified"


def test_executor_error_means_effect_unknown_not_failed_clean():
    gate = _gate()

    def risky(account_id: str) -> None:
        raise RuntimeError("network lost after remote submission")

    with pytest.raises(RuntimeError):
        gate.invoke(agent_id="synthetic-bank-agent", run_id="run-1",
                    tool_name="prepare_transfer", arguments={"account_id": "account-42"},
                    executor=risky, now=NOW)
    receipts = list(gate._receipts.values())
    assert len(receipts) == 1 and receipts[0].status is EffectStatus.UNKNOWN


def test_unknown_or_denied_actions_cannot_be_marked_verified():
    gate = _gate()
    with pytest.raises(ValueError):
        gate.record_effect(EffectReceipt("invented", EffectStatus.CONFIRMED))
    denied = _check(gate, "admin_execute")
    with pytest.raises(ValueError):
        gate.record_effect(EffectReceipt(denied.intent.action_id, EffectStatus.CONFIRMED))


def test_mandate_validation_rejects_unbounded_grants_and_naive_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        _gate(expires_at=datetime(2026, 10, 20))
    with pytest.raises(ValueError, match="wildcard"):
        _gate(allowed_resources=frozenset({"*"}))
    with pytest.raises(ValueError):
        ToolAuthority(EffectType.WRITE)
    with pytest.raises(ValueError):
        ToolAuthority(EffectType.UNKNOWN, fixed_resource="account-42")


def test_reconciler_errors_cannot_assert_verification():
    gate = _gate()
    v = _check(gate)
    receipt = EffectReceipt(v.intent.action_id, EffectStatus.CONFIRMED,
                            evidence_source="bank-sandbox", evidence_reference="tx-001")
    stored = gate.record_effect(receipt, verifier=lambda r: 1 / 0 == 0)
    assert stored.assurance == "unverified"


def test_verified_receipt_cannot_be_replaced():
    gate = _gate()
    v = _check(gate)
    receipt = EffectReceipt(v.intent.action_id, EffectStatus.CONFIRMED,
                            evidence_source="independent-ledger", evidence_reference="entry-1")
    gate.record_effect(receipt, verifier=lambda r: True)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        gate.record_effect(EffectReceipt(v.intent.action_id, EffectStatus.UNKNOWN))
