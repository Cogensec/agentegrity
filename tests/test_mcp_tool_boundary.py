"""MCP server-side tools/call authorization and trust-boundary regressions.

No MCP transport, bank, SaaS account or LLM is connected in these tests.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agentegrity.core.action_security import (
    AuthorizationDenied,
    EffectStatus,
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
from agentegrity.sdk.mcp_boundary import MCPToolBoundary, MCPToolCallError

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


def _boundary(
    *,
    allowed_resources: frozenset[str] = frozenset({"invoice-17", "customer-42"}),
    expires_at: datetime | None = None,
) -> tuple[MCPToolBoundary, list[tuple[str, str]]]:
    effects: list[tuple[str, str]] = []

    async def issue_refund(invoice_id: str) -> str:
        effects.append(("refund", invoice_id))
        return "synthetic-refund-requested"

    async def change_plan(customer_id: str, new_plan: str) -> str:
        effects.append(("plan", customer_id))
        return f"synthetic-change-to-{new_plan}"

    sources = {
        "verified_user": SourceLabel(
            "verified_user", SourceTrust.AUTHORITY, SourceChannel.VERIFIED_USER_REQUEST
        ),
        "tool_response": SourceLabel(
            "tool_response", SourceTrust.DATA_ONLY, SourceChannel.TOOL_RESPONSE
        ),
        "retrieved_page": SourceLabel(
            "retrieved_page", SourceTrust.DATA_ONLY, SourceChannel.RETRIEVAL
        ),
    }
    boundary = TrustBoundaryPolicy(
        sources,
        {
            "issue_refund": frozenset({"invoice_id"}),
            "change_plan": frozenset({"customer_id", "new_plan"}),
        },
    )
    mandate = TaskMandate(
        mandate_id="trusted-mcp-task",
        principal_id="verified-customer",
        agent_id="support-agent",
        run_id="support-run-001",
        expires_at=expires_at or NOW + timedelta(minutes=15),
        allowed_tools=frozenset({"issue_refund", "change_plan"}),
        allowed_resources=allowed_resources,
        allowed_effects=frozenset({EffectType.FINANCIAL_WRITE, EffectType.WRITE}),
    )
    gateway = TrustedActionGateway(
        mandate,
        {
            "issue_refund": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="invoice_id"),
                issue_refund,
            ),
            "change_plan": GatewayTool(
                ToolAuthority(EffectType.WRITE, resource_argument="customer_id"),
                change_plan,
            ),
        },
        trust_boundary=boundary,
        evidence_timeout=timedelta(seconds=30),
    )
    return MCPToolBoundary(gateway), effects


def _trusted(*fields: str) -> ActionProvenance:
    return ActionProvenance(
        control_origins={field: frozenset({"verified_user"}) for field in fields}
    )


def test_authorized_financial_mcp_tool_remains_unverified() -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        result = await bridge.call_tool(
            {"name": "issue_refund", "arguments": {"invoice_id": "invoice-17"}},
            action_id="refund-1",
            provenance=_trusted("invoice_id"),
            now=NOW,
        )
        assert result.result == "synthetic-refund-requested"
        assert result.verdict.allowed
        assert result.receipt.status is EffectStatus.PENDING
        assert not bridge.gateway.gate.effects_verified(["refund-1"])

    asyncio.run(run())
    assert effects == [("refund", "invoice-17")]


def test_mcp_cannot_refund_an_unapproved_invoice() -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        with pytest.raises(AuthorizationDenied, match="resource_not_authorized"):
            await bridge.call_tool(
                {"name": "issue_refund", "arguments": {"invoice_id": "invoice-999"}},
                provenance=_trusted("invoice_id"),
                now=NOW,
            )

    asyncio.run(run())
    assert effects == []


@pytest.mark.parametrize(("tool", "params", "provenance", "reason"), [
    (
        "issue_refund", {"invoice_id": "invoice-17"},
        None, "missing_control_provenance",
    ),
    (
        "issue_refund", {"invoice_id": "invoice-17"},
        ActionProvenance(control_origins={"invoice_id": frozenset({"tool_response"})}),
        "untrusted_control_source",
    ),
    (
        "issue_refund", {"invoice_id": "invoice-17"},
        ActionProvenance(
            control_origins={"invoice_id": frozenset({"verified_user"})},
            data_origins={"invoice_id": frozenset({"retrieved_page"})},
        ),
        "data_promoted_to_control",
    ),
    (
        "change_plan", {"customer_id": "customer-42", "new_plan": "premium"},
        ActionProvenance(
            control_origins={"customer_id": frozenset({"verified_user"})},
        ),
        "missing_control_provenance",
    ),
    (
        "change_plan", {"customer_id": "customer-42", "new_plan": "premium"},
        ActionProvenance(
            control_origins={
                "customer_id": frozenset({"verified_user"}),
                "new_plan": frozenset({"verified_user"}),
            },
            data_origins={"new_plan": frozenset({"tool_response"})},
        ),
        "data_promoted_to_control",
    ),
])
def test_untrusted_content_cannot_grant_mcp_authority(
    tool: str,
    params: dict[str, str],
    provenance: ActionProvenance | None,
    reason: str,
) -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        with pytest.raises(AuthorizationDenied, match=reason):
            await bridge.call_tool(
                {"name": tool, "arguments": params},
                now=NOW,
                provenance=provenance,
            )

    asyncio.run(run())
    assert effects == []


def test_authorized_customer_service_mcp_tool_uses_host_verified_fields() -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        result = await bridge.call_tool(
            {"name": "change_plan",
             "arguments": {"customer_id": "customer-42", "new_plan": "premium"}},
            now=NOW,
            provenance=_trusted("customer_id", "new_plan"),
        )
        assert result.receipt.status is EffectStatus.PENDING

    asyncio.run(run())
    assert effects == [("plan", "customer-42")]


def test_duplicate_host_action_identifier_is_not_replayed() -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        params = {"name": "issue_refund", "arguments": {"invoice_id": "invoice-17"}}
        await bridge.call_tool(params, now=NOW, action_id="host-idempotency-1",
                               provenance=_trusted("invoice_id"))
        with pytest.raises(AuthorizationDenied, match="duplicate_action_id"):
            await bridge.call_tool(params, now=NOW, action_id="host-idempotency-1",
                                   provenance=_trusted("invoice_id"))

    asyncio.run(run())
    assert effects == [("refund", "invoice-17")]


def test_expired_mcp_run_cannot_execute() -> None:
    bridge, effects = _boundary(expires_at=NOW - timedelta(seconds=1))

    async def run() -> None:
        with pytest.raises(AuthorizationDenied, match="mandate_expired"):
            await bridge.call_tool(
                {"name": "issue_refund", "arguments": {"invoice_id": "invoice-17"}},
                now=NOW, provenance=_trusted("invoice_id"),
            )

    asyncio.run(run())
    assert effects == []


def test_unknown_mcp_tool_cannot_bypass_registry() -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        with pytest.raises(AuthorizationDenied, match="tool_not_authorized"):
            await bridge.call_tool(
                {"name": "admin_delete_account", "arguments": {"customer_id": "customer-42"}},
                now=NOW,
            )

    asyncio.run(run())
    assert effects == []


@pytest.mark.parametrize("params", [
    None,
    {},
    {"name": None},
    {"name": " "},
    {"name": 7},
    {"name": "x" * 257},
    {"name": "issue_refund", "arguments": []},
    {"name": "issue_refund", "arguments": None},
    {"name": "issue_refund", "arguments": "not a dict"},
    {"name": "issue_refund", "arguments": {}, "_meta": {"authorized": True}},
    {"name": "issue_refund", "arguments": {}, "agent_id": "privileged"},
    {"name": "issue_refund", "arguments": {}, "approved": True},
])
def test_invalid_tool_calls_never_execute(params: object) -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        with pytest.raises(MCPToolCallError):
            await bridge.call_tool(params, now=NOW)  # type: ignore[arg-type]

    asyncio.run(run())
    assert effects == []


def test_mcp_payload_is_never_a_source_of_identity_or_authority() -> None:
    bridge, effects = _boundary()

    async def run() -> None:
        # Model-supplied tool descriptions cannot authorize an unknown tool;
        # JSON-RPC metadata is not parsed into a mandate or an approval.
        with pytest.raises(AuthorizationDenied, match="tool_not_authorized"):
            await bridge.call_tool(
                {
                    "name": "provision_superadmin",
                    "arguments": {"role": "admin", "approved": True},
                },
                now=NOW,
            )

    asyncio.run(run())
    assert effects == []


def test_boundary_rejects_non_gateway_registration() -> None:
    with pytest.raises(TypeError, match="TrustedActionGateway"):
        MCPToolBoundary(object())  # type: ignore[arg-type]
