"""Offline evidence-delay and contradictory-downstream-result regressions."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agentegrity.core.action_security import (
    ActionSecurityGate,
    EffectReceipt,
    EffectStatus,
    EffectType,
    TaskMandate,
    ToolAuthority,
)

NOW = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)


def _gate(
    timeout: timedelta | None = timedelta(minutes=5),
    *,
    max_depth: int = 0,
) -> ActionSecurityGate:
    return ActionSecurityGate(
        TaskMandate(
            mandate_id="synthetic-banking", principal_id="synthetic-customer",
            agent_id="bank-agent", run_id="bank-run",
            expires_at=NOW + timedelta(hours=1),
            allowed_tools=frozenset({"prepare_transfer", "read_account"}),
            allowed_resources=frozenset({"account-42"}),
            allowed_effects=frozenset({EffectType.FINANCIAL_WRITE, EffectType.READ}),
        ),
        {
            "prepare_transfer": ToolAuthority(
                EffectType.FINANCIAL_WRITE, resource_argument="account_id"
            ),
            "read_account": ToolAuthority(EffectType.READ, resource_argument="account_id"),
        },
        evidence_timeout=timeout, max_delegation_depth=max_depth,
    )


def _start(
    gate: ActionSecurityGate, *,
    tool: str = "prepare_transfer",
    action_id: str | None = None,
):
    return gate.invoke(
        agent_id=gate.mandate.agent_id,
        run_id=gate.mandate.run_id or "",
        tool_name=tool,
        arguments={"account_id": "account-42"},
        executor=lambda account_id: f"synthetic-request:{account_id}",
        action_id=action_id, now=NOW,
    )


def _evidence(
    action_id: str, status: EffectStatus, ref: str = "ledger-001",
    source: str = "bank-ledger",
) -> EffectReceipt:
    return EffectReceipt(
        action_id=action_id,
        status=status,
        evidence_source=source,
        evidence_reference=ref,
        independently_verified=True,  # forged caller flag must not suffice
    )


def _verify(receipt: EffectReceipt) -> bool:
    # Synthetic verified host evidence. Production verifiers MUST interrogate
    # the actual downstream authority and bind to the action, not just check a ref.
    return receipt.evidence_source == "bank-ledger"


def test_pending_receipt_is_not_completed_or_verified():
    gate = _gate()
    result = _start(gate)
    assert result.receipt.status is EffectStatus.PENDING
    assert result.receipt.assurance == "unverified"
    assert gate.effects_verified([result.verdict.intent.action_id]) is False
    assert gate.effects_verified([]) is False
    assert gate.effects_verified(["nonexistent"]) is False
    assert [o.reason for o in gate.effect_observations] == ["execution_started"]


def test_unverified_success_claim_is_ignored_even_with_caller_verified_flag():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    result = gate.record_effect(_evidence(action_id, EffectStatus.CONFIRMED))
    assert result.status is EffectStatus.PENDING
    assert result.independently_verified is False
    assert len(gate.effect_observations) == 1
    assert gate.effects_verified([action_id]) is False


@pytest.mark.parametrize("offset,expected", [(4, 0), (5, 1), (12, 1)])
def test_timeout_threshold_is_explicit_and_idempotent(offset: int, expected: int):
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    elapsed = NOW + timedelta(minutes=offset)
    result = gate.expire_unresolved_effects(now=elapsed)
    assert len(result) == expected
    assert gate.receipt_for(action_id).status is (
        EffectStatus.UNKNOWN if expected else EffectStatus.PENDING
    )
    assert gate.expire_unresolved_effects(now=elapsed) == ()
    assert len(gate.effect_observations) == 1 + expected
    assert not gate.effects_verified([action_id])
    if expected:
        assert gate.effect_observations[-1].reason == "evidence_timeout"


def test_disabled_timeout_does_not_change_receipts():
    gate = _gate(None)
    action_id = _start(gate).verdict.intent.action_id
    assert gate.expire_unresolved_effects(now=NOW + timedelta(hours=5)) == ()
    assert gate.receipt_for(action_id).status is EffectStatus.PENDING


@pytest.mark.parametrize("timeout", [timedelta(0), timedelta(seconds=-1), "5m"])
def test_bad_evidence_timeout_rejected(timeout):
    with pytest.raises(ValueError, match="positive timedelta"):
        _gate(timeout)  # type: ignore[arg-type]


def test_late_verified_success_resolves_timeout_uncertainty():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    gate.expire_unresolved_effects(now=NOW + timedelta(minutes=6))
    confirmed = gate.record_effect(
        _evidence(action_id, EffectStatus.CONFIRMED),
        verifier=_verify,
        now=NOW + timedelta(minutes=30),
    )
    assert confirmed.status is EffectStatus.CONFIRMED
    assert confirmed.assurance == "verified"
    assert gate.effects_verified([action_id])
    assert gate.expire_unresolved_effects(now=NOW + timedelta(hours=1)) == ()
    assert [o.status for o in gate.effect_observations] == [
        EffectStatus.PENDING, EffectStatus.UNKNOWN, EffectStatus.CONFIRMED,
    ]


def test_late_verified_failure_does_not_count_as_success():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    gate.expire_unresolved_effects(now=NOW + timedelta(minutes=7))
    failed = gate.record_effect(
        _evidence(action_id, EffectStatus.FAILED),
        verifier=_verify,
        now=NOW + timedelta(minutes=10),
    )
    assert failed.status is EffectStatus.FAILED
    assert failed.independently_verified is True
    assert failed.assurance == "unverified"
    assert not gate.effects_verified([action_id])
    assert gate.expire_unresolved_effects(now=NOW + timedelta(minutes=40)) == ()


def test_late_conflicting_verified_outcome_is_sticky_and_blocks_success():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    confirmed = _evidence(action_id, EffectStatus.CONFIRMED, "tx-success")
    failed = _evidence(action_id, EffectStatus.FAILED, "tx-failure")
    assert gate.record_effect(confirmed, verifier=_verify).status is EffectStatus.CONFIRMED
    conflicted = gate.record_effect(failed, verifier=_verify)
    assert conflicted.status is EffectStatus.CONFLICTED
    assert conflicted.evidence_reference == "tx-failure"
    assert not gate.effects_verified([action_id])
    assert gate.record_effect(confirmed, verifier=_verify).status is EffectStatus.CONFLICTED
    assert gate.record_effect(
        _evidence(action_id, EffectStatus.CONFIRMED, "third-reference"),
        verifier=_verify,
    ).status is EffectStatus.CONFLICTED
    assert gate.effect_observations[-1].reason == "conflicting_verified_evidence"


def test_same_external_reference_with_opposed_verified_outcomes_conflicts():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    gate.record_effect(_evidence(action_id, EffectStatus.CONFIRMED), verifier=_verify)
    conflict = gate.record_effect(_evidence(action_id, EffectStatus.FAILED), verifier=_verify)
    assert conflict.status is EffectStatus.CONFLICTED
    assert not gate.effects_verified([action_id])


def test_duplicate_identical_verified_evidence_is_idempotent():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    evidence = _evidence(action_id, EffectStatus.CONFIRMED)
    assert gate.record_effect(evidence, verifier=_verify).status is EffectStatus.CONFIRMED
    audit_length = len(gate.effect_observations)
    assert gate.record_effect(evidence, verifier=_verify).status is EffectStatus.CONFIRMED
    assert len(gate.effect_observations) == audit_length


def test_verified_failure_then_late_success_does_not_flip_to_confirmed():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    gate.record_effect(_evidence(action_id, EffectStatus.FAILED, "fail"), verifier=_verify)
    result = gate.record_effect(
        _evidence(action_id, EffectStatus.CONFIRMED, "success"), verifier=_verify,
    )
    assert result.status is EffectStatus.CONFLICTED
    assert not gate.effects_verified([action_id])


def test_unverified_claim_cannot_override_authoritative_outcome():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    gate.record_effect(_evidence(action_id, EffectStatus.CONFIRMED), verifier=_verify)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        gate.record_effect(_evidence(action_id, EffectStatus.FAILED))
    with pytest.raises(ValueError, match="cannot be overwritten"):
        gate.record_effect(EffectReceipt(action_id, EffectStatus.UNKNOWN))
    assert gate.effects_verified([action_id])


@pytest.mark.parametrize("status", [EffectStatus.PENDING, EffectStatus.CONFLICTED])
def test_caller_cannot_assert_sdk_lifecycle_status(status):
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    with pytest.raises(ValueError, match="cannot assert pending or conflicted"):
        gate.record_effect(_evidence(action_id, status))


@pytest.mark.parametrize("source,reference", [
    ("sdk", "ref"), ("", "ref"), ("ledger", None), ("ledger", ""),
])
def test_invalid_evidence_source_cannot_authorize_terminal_state(source, reference):
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    claimed = EffectReceipt(
        action_id, EffectStatus.CONFIRMED, source, reference,
        independently_verified=True,
    )
    assert gate.record_effect(claimed, verifier=lambda evidence: True).status is (
        EffectStatus.PENDING
    )


def test_verifier_failure_keeps_uncertainty_and_cannot_be_spoofed():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    gate.expire_unresolved_effects(now=NOW + timedelta(minutes=5))
    claim = _evidence(action_id, EffectStatus.CONFIRMED)
    assert gate.record_effect(claim, verifier=lambda r: False).status is EffectStatus.UNKNOWN
    assert gate.record_effect(claim, verifier=lambda r: 1 / 0).status is EffectStatus.UNKNOWN
    assert not gate.effects_verified([action_id])


def test_missing_action_and_denied_action_evidence_rejected():
    gate = _gate()
    with pytest.raises(ValueError, match="no authorized action"):
        gate.record_effect(_evidence("fabricated", EffectStatus.CONFIRMED), verifier=_verify)
    denied = gate.check(
        agent_id="bank-agent", run_id="bank-run",
        tool_name="not-listed", arguments={"account_id": "account-42"}, now=NOW,
    )
    with pytest.raises(ValueError, match="no authorized action"):
        gate.record_effect(_evidence(denied.intent.action_id, EffectStatus.CONFIRMED),
                           verifier=_verify)


def test_authorized_but_unexecuted_tool_cannot_generate_evidence_receipt():
    gate = _gate()
    checked = gate.check(
        agent_id="bank-agent", run_id="bank-run",
        tool_name="read_account", arguments={"account_id": "account-42"}, now=NOW,
    )
    assert checked.allowed
    with pytest.raises(ValueError, match="missing pending"):
        gate.record_effect(_evidence(checked.intent.action_id, EffectStatus.CONFIRMED),
                           verifier=_verify)
    assert gate.expire_unresolved_effects(now=NOW + timedelta(minutes=20)) == ()


def test_one_unverified_effect_blocks_completion_of_multiple_effects():
    gate = _gate()
    first = _start(gate, action_id="transfer-1").verdict.intent.action_id
    second = _start(gate, action_id="transfer-2").verdict.intent.action_id
    gate.record_effect(_evidence(first, EffectStatus.CONFIRMED), verifier=_verify)
    assert not gate.effects_verified([first, second])
    gate.record_effect(_evidence(second, EffectStatus.CONFIRMED), verifier=_verify)
    assert gate.effects_verified([first, second])


def test_executor_exception_is_uncertain_then_independent_late_commit_resolves():
    gate = _gate()

    def submit(account_id: str) -> None:
        raise ConnectionError("synthetic network disconnect after transfer request")

    with pytest.raises(ConnectionError):
        gate.invoke(
            agent_id="bank-agent", run_id="bank-run",
            tool_name="prepare_transfer",
            arguments={"account_id": "account-42"},
            executor=submit, action_id="lost-response", now=NOW,
        )
    assert gate.receipt_for("lost-response").status is EffectStatus.UNKNOWN
    confirmed = gate.record_effect(
        _evidence("lost-response", EffectStatus.CONFIRMED), verifier=_verify,
    )
    assert confirmed.status is EffectStatus.CONFIRMED
    assert gate.effects_verified(["lost-response"])


@pytest.mark.asyncio
async def test_async_executor_exception_keeps_uncertainty():
    gate = _gate()

    async def submit(account_id: str) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("timeout after dispatch")

    with pytest.raises(RuntimeError):
        await gate.ainvoke(
            agent_id="bank-agent", run_id="bank-run",
            tool_name="prepare_transfer", arguments={"account_id": "account-42"},
            executor=submit, now=NOW,
        )
    action_id = next(iter(gate._receipts))
    assert gate.receipt_for(action_id).status is EffectStatus.UNKNOWN
    assert not gate.effects_verified([action_id])


def test_multiple_verified_reports_race_to_conflict_safely():
    gate = _gate()
    action_id = _start(gate).verdict.intent.action_id
    reports = [
        _evidence(action_id, EffectStatus.CONFIRMED, "confirmed"),
        _evidence(action_id, EffectStatus.FAILED, "failed"),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda e: gate.record_effect(e, verifier=_verify), reports))
    assert any(r.independently_verified for r in results)
    assert gate.receipt_for(action_id).status is EffectStatus.CONFLICTED
    assert not gate.effects_verified([action_id])


def test_delegated_gate_inherits_parent_evidence_deadline():
    parent = _gate(timedelta(seconds=10), max_depth=1)
    child = parent.delegate(
        agent_id="read-worker", run_id="child-run",
        allowed_tools=frozenset({"read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=NOW + timedelta(minutes=3),
        now=NOW,
    )
    action_id = _start(child, tool="read_account").verdict.intent.action_id
    assert child.expire_unresolved_effects(now=NOW + timedelta(seconds=10))
    assert child.receipt_for(action_id).status is EffectStatus.UNKNOWN
