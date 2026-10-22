"""Offline MCP tools/call reference for a customer-support / payment agent.

This simulates the trusted server dispatch boundary. It does not launch an
MCP server or validate any real banking provider. Only trusted host code
constructs the mandate, source registry, and tool executors.

Run: python examples/mcp_authorized_customer_agent.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agentegrity.core.action_security import (
    AuthorizationDenied,
    EffectType,
    TaskMandate,
    ToolAuthority,
)
from agentegrity.core.trust_boundaries import (
    ActionProvenance,
    SourceChannel,
    SourceLabel,
    SourceTrust,
    TrustBoundaryPolicy,
)
from agentegrity.sdk.gateway import GatewayTool, TrustedActionGateway
from agentegrity.sdk.mcp_boundary import MCPToolBoundary


async def run_demo() -> tuple[str, str, str]:
    """Demonstrate fail-closed control with synthetic, in-memory effects."""
    now = datetime.now(timezone.utc)
    effects: list[str] = []

    async def issue_refund(invoice_id: str) -> str:
        effects.append(f"requested:{invoice_id}")
        return "synthetic-refund-request"

    source_policy = TrustBoundaryPolicy(
        sources={
            "customer_verified": SourceLabel(
                "customer_verified",
                SourceTrust.AUTHORITY,
                SourceChannel.VERIFIED_USER_REQUEST,
            ),
            "mcp_tool_response": SourceLabel(
                "mcp_tool_response", SourceTrust.DATA_ONLY, SourceChannel.TOOL_RESPONSE
            ),
        },
        protected_fields={"issue_refund": frozenset({"invoice_id"})},
    )
    gateway = TrustedActionGateway(
        mandate=TaskMandate(
            mandate_id="approved-synthetic-customer-task",
            principal_id="verified-customer-1",
            agent_id="support-agent-1",
            run_id="run-1",
            expires_at=now + timedelta(minutes=5),
            allowed_tools=frozenset({"issue_refund"}),
            allowed_resources=frozenset({"invoice-42"}),
            allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
        ),
        tools={
            "issue_refund": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="invoice_id"),
                issue_refund,
            ),
        },
        trust_boundary=source_policy,
    )
    mcp = MCPToolBoundary(gateway)
    request = {"name": "issue_refund", "arguments": {"invoice_id": "invoice-42"}}

    # An injection in a retrieved tool response cannot turn that response
    # into a trusted authorization for the invoice parameter.
    data_only_provenance = ActionProvenance(
        control_origins={"invoice_id": frozenset({"mcp_tool_response"})},
    )
    try:
        await mcp.call_tool(
            request, now=now, provenance=data_only_provenance, action_id="attempt-1"
        )
    except AuthorizationDenied as exc:
        assert exc.verdict.reason == "untrusted_control_source"
        denied = exc.verdict.reason
    else:
        raise AssertionError("Untrusted MCP data must not authorize a refund")
    assert effects == []

    trusted_provenance = ActionProvenance(
        control_origins={"invoice_id": frozenset({"customer_verified"})}
    )
    allowed = await mcp.call_tool(
        request, now=now, provenance=trusted_provenance, action_id="attempt-2"
    )
    assert effects == ["requested:invoice-42"]
    assert not gateway.gate.effects_verified(["attempt-2"])
    return denied, allowed.result, allowed.receipt.status.value


if __name__ == "__main__":
    denial, result, receipt = asyncio.run(run_demo())
    print(f"Untrusted tool-origin instruction: {denial}")
    print(f"Host-authorized synthetic tool result: {result}")
    print(f"Downstream effect state: {receipt} (not independently verified)")
