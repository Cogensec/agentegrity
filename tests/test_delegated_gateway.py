"""Delegated agent execution: least privilege, revocation, provenance and receipts.

All agents and data are synthetic; these tests exercise host-owned Python
execution functions rather than remote banking or SaaS authorization.
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

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


def _root(*, depth: int = 2, with_provenance: bool = False):
    effects: list[tuple[str, str]] = []

    def read_document(document_id: str) -> str:
        effects.append(("read", document_id))
        return "synthetic-document"

    def prepare_payment(invoice_id: str) -> str:
        effects.append(("payment", invoice_id))
        return "synthetic-payment-request"

    async def send_message(recipient_id: str) -> str:
        effects.append(("message", recipient_id))
        return "synthetic-message"

    policy = None
    if with_provenance:
        policy = TrustBoundaryPolicy(
            sources={
                "verified-owner": SourceLabel(
                    "verified-owner", SourceTrust.AUTHORITY,
                    SourceChannel.VERIFIED_USER_REQUEST,
                ),
                "retrieved-text": SourceLabel(
                    "retrieved-text", SourceTrust.DATA_ONLY, SourceChannel.RETRIEVAL,
                ),
            },
            protected_fields={
                "read_document": frozenset({"document_id"}),
                "prepare_payment": frozenset({"invoice_id"}),
                "send_message": frozenset({"recipient_id"}),
            },
        )
    root = TrustedActionGateway(
        TaskMandate(
            mandate_id="trusted-enterprise-root",
            principal_id="verified-owner",
            agent_id="coordinator",
            run_id="root-run",
            expires_at=NOW + timedelta(hours=1),
            allowed_tools=frozenset({
                "read_document", "prepare_payment", "send_message",
            }),
            allowed_resources=frozenset({
                "document-1", "document-2", "invoice-9", "recipient-8",
            }),
            allowed_effects=frozenset({
                EffectType.READ, EffectType.FINANCIAL_WRITE, EffectType.EXTERNAL_SEND,
            }),
        ),
        {
            "read_document": GatewayTool(
                ToolAuthority(EffectType.READ, resource_argument="document_id"),
                read_document,
            ),
            "prepare_payment": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="invoice_id"),
                prepare_payment,
            ),
            "send_message": GatewayTool(
                ToolAuthority(EffectType.EXTERNAL_SEND, resource_argument="recipient_id"),
                send_message,
            ),
        },
        trust_boundary=policy,
        max_delegation_depth=depth,
    )
    return root, effects


def _reader(root: TrustedActionGateway, **extra):
    kwargs = {
        "agent_id": "research-worker",
        "run_id": "worker-run",
        "allowed_tools": frozenset({"read_document"}),
        "allowed_resources": frozenset({"document-1"}),
        "allowed_effects": frozenset({EffectType.READ}),
        "expires_at": NOW + timedelta(minutes=30),
        "now": NOW,
    }
    kwargs.update(extra)
    return root.delegate(**kwargs)


def test_child_can_only_execute_delegated_read() -> None:
    root, effects = _root()
    child = _reader(root)
    result = child.invoke(
        "read_document", {"document_id": "document-1"}, now=NOW,
        action_id="read-1",
    )
    assert result.result == "synthetic-document"
    assert result.receipt.status is EffectStatus.PENDING
    assert not child.gate.effects_verified(["read-1"])
    assert child.gate.mandate.principal_id == root.gate.mandate.principal_id
    assert child.gate.mandate.agent_id == "research-worker"
    assert child.gate.mandate.run_id == "worker-run"
    assert effects == [("read", "document-1")]


@pytest.mark.parametrize(
    ("tool", "arguments", "denial"),
    [
        ("prepare_payment", {"invoice_id": "invoice-9"}, "tool_not_authorized"),
        ("send_message", {"recipient_id": "recipient-8"}, "tool_not_authorized"),
        ("read_document", {"document_id": "document-2"}, "resource_not_authorized"),
        ("unknown", {"document_id": "document-1"}, "tool_not_authorized"),
    ],
)
def test_child_cannot_escalate_tool_or_resource_scope(
    tool: str, arguments: dict, denial: str,
) -> None:
    root, effects = _root()
    child = _reader(root)
    with pytest.raises(AuthorizationDenied, match=denial):
        child.invoke(tool, arguments, now=NOW)
    assert effects == []


@pytest.mark.parametrize(
    ("field", "grant"),
    [
        ("allowed_tools", frozenset({"read_document", "admin_delete"})),
        ("allowed_resources", frozenset({"document-1", "customer-secret"})),
        ("allowed_effects", frozenset({EffectType.READ, EffectType.WRITE})),
        ("allowed_credentials", frozenset({"stolen-credential"})),
    ],
)
def test_delegation_cannot_create_authority(field: str, grant: frozenset) -> None:
    root, effects = _root()
    with pytest.raises(ValueError, match="parent subset"):
        _reader(root, **{field: grant})
    assert effects == []


def test_child_cannot_impersonate_ancestor_identity_or_run() -> None:
    root, effects = _root()
    with pytest.raises(ValueError, match="distinct"):
        _reader(root, agent_id="coordinator")
    with pytest.raises(ValueError, match="distinct"):
        _reader(root, run_id="root-run")
    assert effects == []


def test_ancestor_revocation_denies_existing_descendant_before_execution() -> None:
    root, effects = _root()
    child = _reader(root)
    root.gate.revoke()
    with pytest.raises(AuthorizationDenied, match="delegation_revoked"):
        child.invoke("read_document", {"document_id": "document-1"}, now=NOW)
    assert effects == []


def test_child_cannot_extend_parent_expiry() -> None:
    root, effects = _root()
    with pytest.raises(ValueError, match="expiration"):
        _reader(root, expires_at=NOW + timedelta(hours=2))
    child = _reader(root, expires_at=NOW + timedelta(minutes=10))
    with pytest.raises(AuthorizationDenied, match="mandate_expired"):
        child.invoke(
            "read_document", {"document_id": "document-1"},
            now=NOW + timedelta(minutes=11),
        )
    assert effects == []


def test_grandchild_gets_narrower_authority_and_depth_limit() -> None:
    root, effects = _root(depth=2)
    child = _reader(root)
    grandchild = child.delegate(
        agent_id="evidence-worker",
        run_id="grandchild-run",
        allowed_tools=frozenset({"read_document"}),
        allowed_resources=frozenset({"document-1"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )
    assert len(grandchild.gate.delegation_lineage) == 3
    with pytest.raises(ValueError, match="depth"):
        grandchild.delegate(
            agent_id="another-worker", run_id="fourth-run",
            allowed_tools=frozenset({"read_document"}),
            allowed_resources=frozenset({"document-1"}),
            allowed_effects=frozenset({EffectType.READ}),
            expires_at=NOW + timedelta(minutes=5), now=NOW,
        )
    receipt = grandchild.invoke(
        "read_document", {"document_id": "document-1"}, now=NOW
    ).receipt
    assert receipt.status is EffectStatus.PENDING
    root.gate.revoke()
    with pytest.raises(AuthorizationDenied, match="delegation_revoked"):
        grandchild.invoke("read_document", {"document_id": "document-1"}, now=NOW)
    assert effects == [("read", "document-1")]


def test_default_gateway_does_not_delegate_any_authority() -> None:
    root, effects = _root(depth=0)
    with pytest.raises(ValueError, match="depth"):
        _reader(root)
    assert effects == []


def test_child_inherits_host_provenance_boundary() -> None:
    root, effects = _root(with_provenance=True)
    child = _reader(root)
    arguments = {"document_id": "document-1"}
    with pytest.raises(AuthorizationDenied, match="missing_control_provenance"):
        child.invoke("read_document", arguments, now=NOW)
    with pytest.raises(AuthorizationDenied, match="untrusted_control_source"):
        child.invoke(
            "read_document", arguments, now=NOW,
            provenance=ActionProvenance(
                control_origins={"document_id": frozenset({"retrieved-text"})},
            ),
        )
    result = child.invoke(
        "read_document", arguments, now=NOW,
        provenance=ActionProvenance(
            control_origins={"document_id": frozenset({"verified-owner"})},
        ),
    )
    assert result.receipt.status is EffectStatus.PENDING
    assert effects == [("read", "document-1")]


def test_async_child_executes_only_in_authorized_scope() -> None:
    root, effects = _root()
    child = root.delegate(
        agent_id="communications-worker",
        run_id="communications-run",
        allowed_tools=frozenset({"send_message"}),
        allowed_resources=frozenset({"recipient-8"}),
        allowed_effects=frozenset({EffectType.EXTERNAL_SEND}),
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )

    async def run() -> None:
        with pytest.raises(AuthorizationDenied, match="resource_not_authorized"):
            await child.ainvoke("send_message", {"recipient_id": "recipient-9"}, now=NOW)
        assert effects == []
        accepted = await child.ainvoke(
            "send_message", {"recipient_id": "recipient-8"}, now=NOW
        )
        assert accepted.receipt.status is EffectStatus.PENDING
        assert accepted.result == "synthetic-message"

    asyncio.run(run())
    assert effects == [("message", "recipient-8")]
