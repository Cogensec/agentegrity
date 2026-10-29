"""Three-agent synthetic workflow with least-privilege delegation.

No external API is called. Host-generated child authority is scoped to one
document. Agent-supplied instructions cannot manufacture a financial grant.

Run: python examples/delegated_customer_workflow.py
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


def run_demo() -> dict[str, object]:
    now = datetime.now(timezone.utc)
    effects: list[str] = []

    def read_customer_report(report_id: str) -> str:
        effects.append("read:" + report_id)
        return "synthetic-report"

    def authorize_refund(invoice_id: str) -> str:
        effects.append("refund:" + invoice_id)
        return "synthetic-refund-request"

    root = TrustedActionGateway(
        TaskMandate(
            mandate_id="trusted-operator-mandate",
            principal_id="operator",
            agent_id="supervisor",
            run_id="supervisor-run",
            expires_at=now + timedelta(minutes=30),
            allowed_tools=frozenset({"read_customer_report", "authorize_refund"}),
            allowed_resources=frozenset({"report-17", "invoice-42"}),
            allowed_effects=frozenset({EffectType.READ, EffectType.FINANCIAL_WRITE}),
        ),
        {
            "read_customer_report": GatewayTool(
                ToolAuthority(EffectType.READ, resource_argument="report_id"),
                read_customer_report,
            ),
            "authorize_refund": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="invoice_id"),
                authorize_refund,
            ),
        },
        max_delegation_depth=1,
    )
    analyst = root.delegate(
        agent_id="analyst", run_id="analyst-run",
        allowed_tools=frozenset({"read_customer_report"}),
        allowed_resources=frozenset({"report-17"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=now + timedelta(minutes=10),
    )
    read = analyst.invoke("read_customer_report", {"report_id": "report-17"})
    assert read.receipt.status is EffectStatus.PENDING

    try:
        analyst.invoke("authorize_refund", {"invoice_id": "invoice-42"})
    except AuthorizationDenied as exc:
        denied = exc.verdict.reason
    else:
        raise AssertionError("delegated analyst must not initiate refunds")

    assert effects == ["read:report-17"]
    root.gate.revoke()
    try:
        analyst.invoke("read_customer_report", {"report_id": "report-17"})
    except AuthorizationDenied as exc:
        revoked = exc.verdict.reason
    else:
        raise AssertionError("revocation must propagate to every child")

    return {"denied": denied, "revoked": revoked, "effects": effects}


if __name__ == "__main__":
    print(run_demo())
