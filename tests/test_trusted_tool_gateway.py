"""Host-owned enterprise and financial gateway execution regressions."""

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
from agentegrity.sdk.gateway import GatewayTool, TrustedActionGateway

NOW = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)


def _gateway(
    *, allowed_tools: frozenset[str] | None = None,
    allowed_resources: frozenset[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[TrustedActionGateway, list[str]]:
    effects: list[str] = []

    def prepare_transfer(account_id: str, note: str = "") -> str:
        effects.append(f"payment:{account_id}:{note}")
        return "synthetic-pending-id"

    def update_customer(customer_id: str, tier: str) -> str:
        effects.append(f"crm:{customer_id}:{tier}")
        return "synthetic-crm-action"

    mandate = TaskMandate(
        mandate_id="trusted-mandate-1",
        principal_id="verified-host-principal",
        agent_id="agent-1",
        run_id="run-1",
        expires_at=expires_at or NOW + timedelta(minutes=15),
        allowed_tools=allowed_tools or frozenset({"prepare_transfer", "update_customer"}),
        allowed_resources=allowed_resources or frozenset({"acct-42", "customer-7"}),
        allowed_effects=frozenset({EffectType.FINANCIAL_WRITE, EffectType.WRITE}),
    )
    gateway = TrustedActionGateway(
        mandate,
        {
            "prepare_transfer": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="account_id"),
                prepare_transfer,
            ),
            "update_customer": GatewayTool(
                ToolAuthority(EffectType.WRITE, resource_argument="customer_id"),
                update_customer,
            ),
        },
    )
    return gateway, effects


def test_financial_gateway_denies_first_out_of_scope_write() -> None:
    gateway, effects = _gateway()
    with pytest.raises(AuthorizationDenied, match="resource_not_authorized"):
        gateway.invoke("prepare_transfer", {"account_id": "acct-99"}, now=NOW)
    assert effects == []


def test_enterprise_gateway_denies_scope_violation() -> None:
    gateway, effects = _gateway()
    with pytest.raises(AuthorizationDenied, match="resource_not_authorized"):
        gateway.invoke(
            "update_customer", {"customer_id": "customer-8", "tier": "enterprise"}, now=NOW
        )
    assert effects == []


def test_unknown_tool_denied_without_executor() -> None:
    gateway, effects = _gateway()
    with pytest.raises(AuthorizationDenied, match="tool_not_authorized"):
        gateway.invoke("delete_customer", {"customer_id": "customer-7"}, now=NOW)
    assert effects == []


def test_allowed_financial_write_remains_pending() -> None:
    gateway, effects = _gateway()
    result = gateway.invoke(
        "prepare_transfer", {"account_id": "acct-42"}, action_id="action-1", now=NOW
    )
    assert result.result == "synthetic-pending-id"
    assert result.receipt.status is EffectStatus.PENDING
    assert not result.receipt.independently_verified
    assert not gateway.gate.effects_verified(["action-1"])
    assert effects == ["payment:acct-42:"]


def test_duplicate_action_id_cannot_replay_transaction() -> None:
    gateway, effects = _gateway()
    gateway.invoke("prepare_transfer", {"account_id": "acct-42"}, action_id="same", now=NOW)
    with pytest.raises(AuthorizationDenied, match="duplicate_action_id"):
        gateway.invoke("prepare_transfer", {"account_id": "acct-42"}, action_id="same", now=NOW)
    assert effects == ["payment:acct-42:"]


def test_expired_mandate_cannot_call_executor() -> None:
    gateway, effects = _gateway(expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(AuthorizationDenied, match="mandate_expired"):
        gateway.invoke("prepare_transfer", {"account_id": "acct-42"}, now=NOW)
    assert effects == []


def test_mandate_registry_is_snapshot_of_host_registration() -> None:
    gateway, effects = _gateway()
    result = gateway.invoke(
        "update_customer", {"customer_id": "customer-7", "tier": "gold"}, now=NOW
    )
    assert result.receipt.status is EffectStatus.PENDING
    assert effects == ["crm:customer-7:gold"]


def test_non_json_arguments_rejected_without_executing() -> None:
    gateway, effects = _gateway()
    with pytest.raises(ValueError, match="JSON objects"):
        gateway.invoke("prepare_transfer", {"account_id": {"bad": object()}}, now=NOW)
    with pytest.raises(ValueError, match="size limit"):
        gateway.invoke(
            "prepare_transfer", {"account_id": "acct-42", "note": "x" * 70000}, now=NOW
        )
    with pytest.raises(ValueError, match="finite JSON"):
        gateway.invoke("prepare_transfer", {"account_id": float("nan")}, now=NOW)
    with pytest.raises(ValueError, match="argument keys"):
        gateway.invoke(
            "prepare_transfer", {"account_id": "acct-42", "note": [{1: "bad"}]}, now=NOW
        )
    assert effects == []


def test_sync_executor_error_marks_effect_unknown() -> None:
    def fail_after_side_effect(account_id: str) -> str:
        raise RuntimeError("downstream timeout after unknown settlement")

    mandate = TaskMandate(
        mandate_id="mandate-2",
        principal_id="principal",
        agent_id="agent-1",
        run_id="run-1",
        expires_at=NOW + timedelta(minutes=5),
        allowed_tools=frozenset({"prepare_transfer"}),
        allowed_resources=frozenset({"acct-42"}),
        allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
    )
    gateway = TrustedActionGateway(
        mandate,
        {
            "prepare_transfer": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="account_id"),
                fail_after_side_effect,
            )
        },
    )
    with pytest.raises(RuntimeError, match="downstream timeout"):
        gateway.invoke(
            "prepare_transfer", {"account_id": "acct-42"},
            action_id="timeout-action", now=NOW
        )
    assert gateway.gate.receipt_for("timeout-action").status is EffectStatus.UNKNOWN


def test_async_gateway_enforces_before_execution() -> None:
    calls: list[str] = []

    async def transfer(account_id: str) -> str:
        calls.append(account_id)
        return "request-queued"

    mandate = TaskMandate(
        mandate_id="mandate-3",
        principal_id="principal",
        agent_id="agent-1",
        run_id="run-1",
        expires_at=NOW + timedelta(minutes=5),
        allowed_tools=frozenset({"transfer"}),
        allowed_resources=frozenset({"acct-42"}),
        allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
    )
    gateway = TrustedActionGateway(
        mandate,
        {
            "transfer": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="account_id"),
                transfer,
            )
        },
    )

    async def exercise() -> None:
        with pytest.raises(AuthorizationDenied, match="resource_not_authorized"):
            await gateway.ainvoke("transfer", {"account_id": "acct-99"}, now=NOW)
        assert calls == []
        allowed = await gateway.ainvoke("transfer", {"account_id": "acct-42"}, now=NOW)
        assert allowed.receipt.status is EffectStatus.PENDING
        with pytest.raises(TypeError, match="async executor"):
            gateway.invoke("transfer", {"account_id": "acct-42"}, now=NOW)

    asyncio.run(exercise())
    assert calls == ["acct-42"]


def test_gateway_rejects_unbound_run() -> None:
    mandate = TaskMandate(
        mandate_id="mandate-4",
        principal_id="principal",
        agent_id="agent",
        run_id=None,
        expires_at=NOW + timedelta(minutes=5),
        allowed_tools=frozenset(),
        allowed_resources=frozenset(),
        allowed_effects=frozenset(),
    )
    with pytest.raises(ValueError, match="run-bound"):
        TrustedActionGateway(mandate, {})
