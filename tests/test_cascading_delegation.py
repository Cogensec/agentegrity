"""Synthetic subagent delegation: attenuation, revocation, provenance and replay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentegrity.core.action_security import (
    ActionSecurityGate,
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

NOW = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)


def _root(
    *,
    max_depth: int = 2,
    with_boundary: bool = False,
    not_before: datetime | None = None,
) -> ActionSecurityGate:
    mandate = TaskMandate(
        mandate_id="synthetic-parent",
        principal_id="customer-001",
        agent_id="bank-supervisor",
        run_id="parent-run",
        expires_at=NOW + timedelta(hours=1),
        not_before=not_before,
        allowed_tools=frozenset({"read_account", "prepare_transfer", "send_message"}),
        allowed_resources=frozenset({"account-42", "account-99"}),
        allowed_effects=frozenset({
            EffectType.READ, EffectType.FINANCIAL_WRITE, EffectType.EXTERNAL_SEND
        }),
        allowed_credentials=frozenset({"bank-short-lived", "messaging-ephemeral"}),
        credential_expires_at=NOW + timedelta(minutes=20),
    )
    boundary = None
    if with_boundary:
        boundary = TrustBoundaryPolicy(
            sources={
                "verified-user": SourceLabel(
                    "verified-user", SourceTrust.AUTHORITY,
                    SourceChannel.VERIFIED_USER_REQUEST,
                ),
                "retrieved-invoice": SourceLabel(
                    "retrieved-invoice", SourceTrust.DATA_ONLY, SourceChannel.RETRIEVAL,
                ),
            },
            protected_fields={
                "read_account": frozenset({"account_id"}),
                "prepare_transfer": frozenset({"account_id", "destination"}),
                "send_message": frozenset({"recipient"}),
            },
        )
    return ActionSecurityGate(
        mandate,
        {
            "read_account": ToolAuthority(EffectType.READ, resource_argument="account_id"),
            "prepare_transfer": ToolAuthority(
                EffectType.FINANCIAL_WRITE, resource_argument="account_id",
                credential_id="bank-short-lived",
            ),
            "send_message": ToolAuthority(
                EffectType.EXTERNAL_SEND, fixed_resource="account-99",
                credential_id="messaging-ephemeral",
            ),
        },
        trust_boundary=boundary,
        max_delegation_depth=max_depth,
    )


def _child(root: ActionSecurityGate, **overrides: object) -> ActionSecurityGate:
    request = dict(
        agent_id="reader-worker",
        run_id="child-run",
        allowed_tools=frozenset({"read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=NOW + timedelta(minutes=30),
        now=NOW + timedelta(minutes=1),
    )
    request.update(overrides)
    return root.delegate(**request)  # type: ignore[arg-type]


def _check(gate: ActionSecurityGate, *, tool: str = "read_account", **fields: object):
    return gate.check(
        agent_id=gate.mandate.agent_id,
        run_id=gate.mandate.run_id or "absent-run",
        tool_name=tool,
        arguments={"account_id": "account-42", **fields},
        now=NOW + timedelta(minutes=4),
    )


def test_delegated_grant_retains_principal_and_binds_new_identity():
    root = _root()
    child = _child(root)
    assert child.mandate.principal_id == root.mandate.principal_id
    assert child.mandate.agent_id == "reader-worker"
    assert child.mandate.run_id == "child-run"
    assert child.delegation_lineage[0] == "synthetic-parent"
    assert child.delegation_lineage[-1] == child.mandate.mandate_id
    assert child.mandate.mandate_id != root.mandate.mandate_id
    assert _check(child).allowed


def test_child_cannot_access_undelegated_tool_or_resource():
    child = _child(_root())
    assert _check(child, tool="prepare_transfer").reason == "tool_not_authorized"
    assert _check(child, account_id="account-99").reason == "resource_not_authorized"
    assert child._tools.keys() == {"read_account"}


@pytest.mark.parametrize(
    ("field_name", "expanded"),
    [
        ("allowed_tools", frozenset({"read_account", "admin_shell"})),
        ("allowed_resources", frozenset({"account-42", "account-500"})),
        ("allowed_effects", frozenset({EffectType.READ, EffectType.WRITE})),
        ("allowed_credentials", frozenset({"unregistered-token"})),
    ],
)
def test_delegation_rejects_authority_amplification(field_name, expanded):
    root = _root()
    with pytest.raises(ValueError, match="parent subset"):
        _child(root, **{field_name: expanded})
    assert _check(root).allowed


def test_subagent_cannot_impersonate_parent_agent_or_run():
    root = _root()
    with pytest.raises(ValueError, match="distinct"):
        _child(root, agent_id="bank-supervisor")
    with pytest.raises(ValueError, match="distinct"):
        _child(root, run_id="parent-run")
    child = _child(root)
    assert child.check(
        agent_id="bank-supervisor", run_id="child-run",
        tool_name="read_account", arguments={"account_id": "account-42"},
        now=NOW + timedelta(minutes=2),
    ).reason == "agent_identity_mismatch"
    assert child.check(
        agent_id="reader-worker", run_id="parent-run",
        tool_name="read_account", arguments={"account_id": "account-42"},
        now=NOW + timedelta(minutes=2),
    ).reason == "run_identity_mismatch"


def test_delegation_disabled_without_explicit_max_depth():
    root = _root(max_depth=0)
    with pytest.raises(ValueError, match="depth"):
        _child(root)


@pytest.mark.parametrize("depth", [-1, 9, True, "2"])
def test_invalid_delegation_depths_rejected(depth):
    root = _root()
    with pytest.raises(ValueError, match="max_delegation_depth"):
        ActionSecurityGate(root.mandate, root._tools, max_delegation_depth=depth)


def test_nested_delegation_cannot_reexpand_scopes_or_bypass_depth():
    root = _root(max_depth=2)
    child = _child(root)
    grandchild = child.delegate(
        agent_id="read-only-assistant",
        run_id="grandchild-run",
        allowed_tools=frozenset({"read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=NOW + timedelta(minutes=15),
        now=NOW + timedelta(minutes=2),
    )
    assert len(grandchild.delegation_lineage) == 3
    assert _check(grandchild).allowed
    with pytest.raises(ValueError, match="parent subset"):
        grandchild.delegate(
            agent_id="another-agent", run_id="another-run",
            allowed_tools=frozenset({"prepare_transfer"}),
            allowed_resources=frozenset({"account-99"}),
            allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
            expires_at=NOW + timedelta(minutes=10),
            now=NOW + timedelta(minutes=3),
        )
    with pytest.raises(ValueError, match="depth"):
        grandchild.delegate(
            agent_id="another-agent", run_id="another-run",
            allowed_tools=frozenset({"read_account"}),
            allowed_resources=frozenset({"account-42"}),
            allowed_effects=frozenset({EffectType.READ}),
            expires_at=NOW + timedelta(minutes=10),
            now=NOW + timedelta(minutes=3),
        )


def test_expiration_cannot_extend_parent_or_be_expired_at_issue_time():
    root = _root()
    with pytest.raises(ValueError, match="expiration"):
        _child(root, expires_at=NOW + timedelta(hours=2))
    with pytest.raises(ValueError, match="expiration"):
        _child(root, expires_at=NOW)
    child = _child(root, expires_at=NOW + timedelta(minutes=5))
    assert _check(child).allowed
    assert child.check(
        agent_id="reader-worker", run_id="child-run",
        tool_name="read_account", arguments={"account_id": "account-42"},
        now=NOW + timedelta(minutes=6),
    ).reason == "ancestor_mandate_expired"


def test_not_before_inherits_and_never_precedes_parent():
    root = _root(not_before=NOW + timedelta(minutes=2))
    with pytest.raises(PermissionError, match="parent mandate"):
        _child(root)
    with pytest.raises(ValueError, match="before parent"):
        _child(root, now=NOW + timedelta(minutes=3), not_before=NOW)
    child = _child(root, now=NOW + timedelta(minutes=3))
    assert child.mandate.not_before == root.mandate.not_before
    assert _check(child).allowed


def test_credential_expiry_cannot_be_extended_by_delegation():
    root = _root()
    with pytest.raises(ValueError, match="extend"):
        _child(root, credential_expires_at=NOW + timedelta(minutes=25))
    child = _child(
        root,
        allowed_tools=frozenset({"prepare_transfer"}),
        allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
        allowed_credentials=frozenset({"bank-short-lived"}),
    )
    assert child.mandate.credential_expires_at == root.mandate.credential_expires_at
    assert _check(child, tool="prepare_transfer").allowed
    assert child.check(
        agent_id="reader-worker", run_id="child-run",
        tool_name="prepare_transfer", arguments={"account_id": "account-42"},
        now=NOW + timedelta(minutes=21),
    ).reason == "credential_expired"


def test_parent_revoke_blocks_descendant_sync_and_async_execution_without_receipts():
    root = _root()
    child = _child(root)
    calls: list[str] = []
    root.revoke()
    with pytest.raises(AuthorizationDenied, match="delegation_revoked"):
        child.invoke(
            agent_id="reader-worker", run_id="child-run",
            tool_name="read_account", arguments={"account_id": "account-42"},
            executor=lambda account_id: calls.append(account_id),
            now=NOW + timedelta(minutes=4),
        )
    assert calls == []
    assert child._receipts == {}


@pytest.mark.asyncio
async def test_parent_revoke_blocks_async_tool_before_it_runs():
    root = _root()
    child = _child(root)
    called: list[str] = []

    async def reader(account_id: str) -> str:
        called.append(account_id)
        return account_id

    root.revoke()
    with pytest.raises(AuthorizationDenied, match="delegation_revoked"):
        await child.ainvoke(
            agent_id="reader-worker", run_id="child-run",
            tool_name="read_account", arguments={"account_id": "account-42"},
            executor=reader, now=NOW + timedelta(minutes=4),
        )
    assert called == []
    assert not child._receipts


def test_revocation_after_check_blocks_before_effect_begins():
    root = _root()
    child = _child(root)
    verdict = _check(child)
    assert verdict.allowed
    root.revoke()
    with pytest.raises(AuthorizationDenied, match="delegation_revoked"):
        child._begin_execution(verdict)
    assert child.receipt_for(verdict.intent.action_id) is None


def test_revoking_child_does_not_revoke_parent_or_sibling():
    root = _root()
    child = _child(root)
    sibling = _child(root, agent_id="second-worker", run_id="second-run")
    child.revoke()
    assert _check(child).reason == "delegation_revoked"
    assert _check(root).allowed
    assert _check(sibling).allowed


def test_nested_ancestor_revocation_blocks_grandchild():
    root = _root()
    child = _child(root)
    grandchild = child.delegate(
        agent_id="grandchild", run_id="grandchild-run",
        allowed_tools=frozenset({"read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ}),
        expires_at=NOW + timedelta(minutes=15),
        now=NOW + timedelta(minutes=2),
    )
    child.revoke()
    assert _check(grandchild).reason == "delegation_revoked"


def test_child_cannot_discard_trust_boundary_or_launder_peer_instruction():
    root = _root(with_boundary=True)
    child = _child(root)
    lineage = ActionProvenance(
        control_origins={"account_id": frozenset({"retrieved-invoice"})}
    )
    result = child.check(
        agent_id="reader-worker", run_id="child-run",
        tool_name="read_account", arguments={"account_id": "account-42"},
        provenance=lineage, now=NOW + timedelta(minutes=4),
    )
    assert result.reason == "untrusted_control_source"
    assert child._trust_boundary is root._trust_boundary


def test_child_keeps_effect_receipts_unverified():
    root = _root()
    child = _child(root)
    completed = child.invoke(
        agent_id="reader-worker", run_id="child-run",
        tool_name="read_account", arguments={"account_id": "account-42"},
        executor=lambda account_id: "synthetic-only", now=NOW + timedelta(minutes=4),
    )
    assert completed.result == "synthetic-only"
    assert completed.receipt.status is EffectStatus.PENDING
    assert completed.receipt.assurance == "unverified"
    assert child.receipt_for(completed.verdict.intent.action_id) is not None


def test_delegation_issued_only_while_parent_is_active_and_not_revoked():
    root = _root()
    root.revoke()
    with pytest.raises(PermissionError, match="parent mandate"):
        _child(root)
    root = _root()
    with pytest.raises(PermissionError, match="parent mandate"):
        _child(root, now=NOW + timedelta(hours=2))


def test_unbound_parent_session_cannot_delegate():
    root = _root()
    mandate = TaskMandate(
        mandate_id="unbound",
        principal_id=root.mandate.principal_id,
        agent_id=root.mandate.agent_id,
        run_id=None,
        allowed_tools=root.mandate.allowed_tools,
        allowed_resources=root.mandate.allowed_resources,
        allowed_effects=root.mandate.allowed_effects,
        expires_at=root.mandate.expires_at,
    )
    unbound = ActionSecurityGate(mandate, root._tools, max_delegation_depth=2)
    with pytest.raises(ValueError, match="run-bound"):
        _child(unbound)


def test_malformed_requested_grant_is_not_implicitly_cast():
    root = _root()
    with pytest.raises(ValueError, match="parent subset"):
        _child(root, allowed_tools={"read_account"})
    with pytest.raises(ValueError, match="agent_id"):
        _child(root, agent_id=" ")
    with pytest.raises(ValueError, match="run_id"):
        _child(root, run_id=" ")
