"""Offline Runtime Action Authority example with synthetic evidence.

Run from a checkout with Agentegrity installed:
    python examples/action_authority_evidence.py

The ledger below is an in-memory stand-in for a trusted downstream service.
It is NOT independent or durable evidence, and is NOT production-grade
verification. A production verifier must query the real acting system.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agentegrity.core.action_security import (
    ActionSecurityGate,
    AuthorizationDenied,
    EffectReceipt,
    EffectStatus,
    EffectType,
    TaskMandate,
    ToolAuthority,
)


def run_demo() -> tuple[str, str, str]:
    """Exercise denial -> pending -> simulated verification without a live bank."""
    now = datetime.now(timezone.utc)
    principal_id = "synthetic-customer"
    run_id = "synthetic-run"
    action_id = f"synthetic-action-{uuid4().hex}"

    # This dictionary stands in for the bank's system of record. In production,
    # the authorized host verifier MUST independently query the acting service.
    ledger: dict[str, dict[str, str]] = {}

    def synthetic_bank_transfer(account_id: str) -> str:
        reference = f"synthetic-ledger-entry-{action_id}"
        ledger[action_id] = {
            "reference": reference,
            "account_id": account_id,
            "run_id": run_id,
            "principal_id": principal_id,
            "effect": EffectType.FINANCIAL_WRITE.value,
            "result": "confirmed",
        }
        return reference

    gate = ActionSecurityGate(
        TaskMandate(
            mandate_id="synthetic-task",
            principal_id=principal_id,
            agent_id="synthetic-agent",
            run_id=run_id,
            expires_at=now + timedelta(minutes=30),
            allowed_tools=frozenset({"prepare_transfer"}),
            allowed_resources=frozenset({"account-42"}),
            allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
        ),
        {
            "prepare_transfer": ToolAuthority(
                EffectType.FINANCIAL_WRITE, resource_argument="account_id"
            ),
        },
        evidence_timeout=timedelta(minutes=5),
    )

    # Resource scope is enforced at the actual invocation boundary.
    try:
        gate.invoke(
            agent_id="synthetic-agent",
            run_id=run_id,
            tool_name="prepare_transfer",
            arguments={"account_id": "account-99"},
            executor=synthetic_bank_transfer,
        )
    except AuthorizationDenied as exc:
        assert exc.verdict.reason == "resource_not_authorized"
    else:
        raise AssertionError("Out-of-scope transfer must be denied")
    assert ledger == {}, "denied tool calls must not invoke the executor"

    execution = gate.invoke(
        agent_id="synthetic-agent",
        run_id=run_id,
        tool_name="prepare_transfer",
        arguments={"account_id": "account-42"},
        executor=synthetic_bank_transfer,
        action_id=action_id,
    )
    assert execution.receipt.status is EffectStatus.PENDING
    assert not gate.effects_verified([action_id])
    before = execution.receipt.status.value

    claim = EffectReceipt(
        action_id=action_id,
        status=EffectStatus.CONFIRMED,
        evidence_source="synthetic-ledger",
        evidence_reference=execution.result,
    )
    # A tool or model-provided claim is NOT confirmation.
    assert gate.record_effect(claim).status is EffectStatus.PENDING

    def verify_from_synthetic_ledger(candidate: EffectReceipt) -> bool:
        """Illustration only: production MUST authenticate an external lookup."""
        record = ledger.get(candidate.action_id)
        return bool(
            record is not None
            and candidate.status is EffectStatus.CONFIRMED
            and candidate.evidence_source == "synthetic-ledger"
            and record["reference"] == candidate.evidence_reference
            and record["account_id"] == "account-42"
            and record["run_id"] == run_id
            and record["principal_id"] == principal_id
            and record["effect"] == EffectType.FINANCIAL_WRITE.value
            and record["result"] == "confirmed"
        )

    reconciled = gate.record_effect(
        claim, verifier=verify_from_synthetic_ledger,
    )
    assert reconciled.status is EffectStatus.CONFIRMED
    assert gate.effects_verified([action_id])
    assert reconciled.assurance == "verified"
    return "denied", before, reconciled.status.value


if __name__ == "__main__":
    denied, before, after = run_demo()
    print(f"Out-of-scope action: {denied}")
    print(f"Before simulated evidence verification: {before}")
    print(f"After simulated evidence verification: {after}")
