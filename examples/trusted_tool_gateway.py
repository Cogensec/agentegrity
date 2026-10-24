"""Synthetic cross-domain gateway example: denied write, then pending receipt.

This does not contact banks or CRMs and does not independently verify effects.
Run from an installed source checkout: python examples/trusted_tool_gateway.py
"""

from datetime import datetime, timedelta, timezone

from agentegrity import (
    AuthorizationDenied,
    EffectStatus,
    EffectType,
    GatewayTool,
    TaskMandate,
    ToolAuthority,
    TrustedActionGateway,
)


def run_demo() -> tuple[str, str, str]:
    now = datetime.now(timezone.utc)
    journal: list[str] = []

    def synthetic_payment(account_id: str) -> str:
        journal.append(account_id)
        return "simulated-request-42"

    gateway = TrustedActionGateway(
        TaskMandate(
            mandate_id="approved-task",
            principal_id="synthetic-operator",
            agent_id="synthetic-financial-agent",
            run_id="synthetic-run",
            expires_at=now + timedelta(minutes=10),
            allowed_tools=frozenset({"prepare_payment"}),
            allowed_resources=frozenset({"acct-42"}),
            allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
        ),
        {
            "prepare_payment": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="account_id"),
                synthetic_payment,
            )
        },
    )

    try:
        gateway.invoke("prepare_payment", {"account_id": "acct-999"})
    except AuthorizationDenied as exc:
        assert exc.verdict.reason == "resource_not_authorized"
    else:
        raise AssertionError("out-of-scope account must be denied")
    assert journal == []

    allowed = gateway.invoke("prepare_payment", {"account_id": "acct-42"})
    assert allowed.receipt.status is EffectStatus.PENDING
    assert journal == ["acct-42"]
    # This journal is NOT an authenticated downstream system-of-record.
    # A successful return does not prove actual settlement.
    assert not gateway.gate.effects_verified([allowed.verdict.intent.action_id])
    return "denied", allowed.receipt.status.value, "unverified"


if __name__ == "__main__":
    denied, receipt, assurance = run_demo()
    print(f"Out-of-scope payment: {denied}")
    print(f"Authorized tool response: {receipt} (downstream effect {assurance})")
