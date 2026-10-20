"""Deterministic synthetic tests: unclear authority and data-as-control boundaries."""

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


def _sources() -> dict[str, SourceLabel]:
    return {
        "verified-request": SourceLabel(
            "verified-request", SourceTrust.AUTHORITY, SourceChannel.VERIFIED_USER_REQUEST
        ),
        "operator-policy": SourceLabel(
            "operator-policy", SourceTrust.AUTHORITY, SourceChannel.OPERATOR_POLICY
        ),
        "retrieval-17": SourceLabel(
            "retrieval-17", SourceTrust.DATA_ONLY, SourceChannel.RETRIEVAL
        ),
        "mcp-result": SourceLabel(
            "mcp-result", SourceTrust.DATA_ONLY, SourceChannel.TOOL_RESPONSE
        ),
        "peer-message": SourceLabel(
            "peer-message", SourceTrust.DATA_ONLY, SourceChannel.PEER_AGENT
        ),
    }


def _policy(
    protected: dict[str, frozenset[str]] | None = None,
    sources: dict[str, SourceLabel] | None = None,
) -> TrustBoundaryPolicy:
    return TrustBoundaryPolicy(
        sources=_sources() if sources is None else sources,
        protected_fields=protected if protected is not None else {
            "prepare_transfer": frozenset({"account_id", "destination"}),
            "read_account": frozenset({"account_id"}),
        },
    )


def _gate(policy: TrustBoundaryPolicy | None = None) -> ActionSecurityGate:
    mandate = TaskMandate(
        mandate_id="mandate-1", principal_id="synthetic-user",
        agent_id="bank-agent", run_id="run-1",
        expires_at=NOW + timedelta(minutes=5),
        allowed_tools=frozenset({"prepare_transfer", "read_account"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ, EffectType.FINANCIAL_WRITE}),
    )
    return ActionSecurityGate(
        mandate,
        {
            "prepare_transfer": ToolAuthority(
                EffectType.FINANCIAL_WRITE, resource_argument="account_id"
            ),
            "read_account": ToolAuthority(EffectType.READ, resource_argument="account_id"),
        },
        trust_boundary=policy if policy is not None else _policy(),
    )


def _trusted() -> ActionProvenance:
    return ActionProvenance(
        control_origins={
            "account_id": frozenset({"verified-request"}),
            "destination": frozenset({"verified-request"}),
        }
    )


def _transfer(
    gate: ActionSecurityGate,
    provenance: ActionProvenance | None,
    *,
    action_id: str | None = None,
    executor=None,
):
    return gate.invoke(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01"},
        provenance=provenance, now=NOW, action_id=action_id,
        executor=executor if executor is not None else (
            lambda account_id, destination: destination
        ),
    )


def test_authorized_user_request_can_control_transfer_destination():
    result = _transfer(_gate(), _trusted())
    assert result.result == "merchant-01"
    assert result.receipt.status is EffectStatus.PENDING
    assert result.receipt.assurance == "unverified"


@pytest.mark.parametrize(
    ("tainted_source", "expected_reason"),
    [
        ("retrieval-17", "untrusted_control_source"),
        ("mcp-result", "untrusted_control_source"),
        ("peer-message", "untrusted_control_source"),
        ("forged-system-message", "unknown_control_source"),
    ],
)
def test_lower_trust_sources_cannot_choose_recipient(tainted_source: str, expected_reason: str):
    gate = _gate()
    effects: list[str] = []
    lineage = ActionProvenance(control_origins={
        "account_id": frozenset({"verified-request"}),
        "destination": frozenset({tainted_source}),
    })
    with pytest.raises(AuthorizationDenied, match=expected_reason):
        _transfer(gate, lineage, executor=lambda account_id, destination: effects.append(destination))
    assert effects == []
    assert not gate._receipts


def test_claimed_operator_override_inside_retrieved_text_remains_untrusted():
    lineage = ActionProvenance(control_origins={
        "account_id": frozenset({"verified-request"}),
        # A document may *say* it is system-level; only the host registry matters.
        "destination": frozenset({"retrieval-17"}),
    })
    verdict = _gate().check(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01",
                   "role": "system", "approved": True},
        provenance=lineage, now=NOW,
    )
    assert verdict.reason == "untrusted_control_source"


def test_mixed_trusted_and_untrusted_control_origins_deny():
    lineage = ActionProvenance(control_origins={
        "account_id": frozenset({"verified-request"}),
        "destination": frozenset({"verified-request", "mcp-result"}),
    })
    assert _gate().check(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01"},
        provenance=lineage, now=NOW,
    ).reason == "untrusted_control_source"


def test_data_laundered_into_protected_field_denied_even_with_trusted_control_origin():
    lineage = ActionProvenance(
        control_origins=_trusted().control_origins,
        data_origins={"destination": frozenset({"retrieval-17"})},
    )
    verdict = _gate().check(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01"},
        provenance=lineage, now=NOW,
    )
    assert verdict.reason == "data_promoted_to_control"


def test_untrusted_document_as_ordinary_data_is_permitted():
    gate = _gate()
    provenance = ActionProvenance(
        control_origins=_trusted().control_origins,
        data_origins={"notes": frozenset({"retrieval-17"})},
    )
    result = gate.invoke(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01",
                   "notes": "External invoice summary: synthetic only"},
        provenance=provenance, now=NOW,
        executor=lambda account_id, destination, notes: notes,
    )
    assert result.result.startswith("External invoice summary")
    assert result.receipt.assurance == "unverified"


def test_missing_provenance_denies_before_execution():
    gate = _gate()
    with pytest.raises(AuthorizationDenied, match="missing_control_provenance"):
        _transfer(gate, None)
    assert not gate._receipts


def test_missing_single_required_lineage_denied():
    gate = _gate()
    lineage = ActionProvenance(control_origins={
        "account_id": frozenset({"verified-request"}),
    })
    assert gate.check(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01"},
        provenance=lineage, now=NOW,
    ).reason == "missing_control_provenance"


def test_missing_protected_argument_denied_to_avoid_implicit_tool_defaults():
    gate = _gate()
    assert gate.check(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42"},
        provenance=_trusted(), now=NOW,
    ).reason == "protected_field_missing"


def test_tool_without_a_trust_boundary_policy_entry_denied():
    gate = _gate(_policy(protected={"read_account": frozenset({"account_id"})}))
    assert gate.check(
        agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
        arguments={"account_id": "account-42", "destination": "merchant-01"},
        provenance=_trusted(), now=NOW,
    ).reason == "tool_boundary_unregistered"


def test_explicitly_reviewed_read_only_tool_can_opt_out_of_provenance_checks():
    gate = _gate(_policy(protected={"read_account": frozenset(),
                                   "prepare_transfer": frozenset({"destination"})}))
    verdict = gate.check(
        agent_id="bank-agent", run_id="run-1", tool_name="read_account",
        arguments={"account_id": "account-42"}, provenance=None, now=NOW,
    )
    assert verdict.allowed


def test_origin_snapshots_cannot_be_mutated_after_policy_construction():
    source_registry = _sources()
    policy = _policy(sources=source_registry)
    source_registry["retrieval-17"] = SourceLabel(
        "retrieval-17", SourceTrust.DATA_ONLY, SourceChannel.TOOL_RESPONSE
    )
    lineage_data = {"destination": frozenset({"retrieval-17"}),
                    "account_id": frozenset({"verified-request"})}
    provenance = ActionProvenance(control_origins=lineage_data)
    lineage_data["destination"] = frozenset({"verified-request"})
    assert policy.evaluate(
        "prepare_transfer",
        {"account_id": "account-42", "destination": "merchant-01"}, provenance,
    ).reason == "untrusted_control_source"


@pytest.mark.parametrize("channel", [
    SourceChannel.RETRIEVAL, SourceChannel.TOOL_RESPONSE,
    SourceChannel.PEER_AGENT, SourceChannel.MEMORY,
])
def test_lower_trust_channels_cannot_claim_authority(channel: SourceChannel):
    with pytest.raises(ValueError, match="cannot grant"):
        SourceLabel("forged", SourceTrust.AUTHORITY, channel)


@pytest.mark.asyncio
async def test_async_tool_not_called_when_retrieval_controls_destination():
    gate = _gate()
    called: list[str] = []

    async def send(account_id: str, destination: str) -> str:
        called.append(destination)
        return destination

    with pytest.raises(AuthorizationDenied, match="untrusted_control_source"):
        await gate.ainvoke(
            agent_id="bank-agent", run_id="run-1", tool_name="prepare_transfer",
            arguments={"account_id": "account-42", "destination": "merchant-01"},
            provenance=ActionProvenance(control_origins={
                "account_id": frozenset({"verified-request"}),
                "destination": frozenset({"retrieval-17"}),
            }), now=NOW, executor=send,
        )
    assert called == []


def test_existing_action_id_replay_still_denied_with_boundary_enabled():
    gate = _gate()
    _transfer(gate, _trusted(), action_id="transfer-001")
    with pytest.raises(AuthorizationDenied, match="duplicate_action_id"):
        _transfer(gate, _trusted(), action_id="transfer-001")


def test_malformed_provenance_and_registry_rejected():
    with pytest.raises(TypeError, match="frozenset"):
        ActionProvenance(control_origins={"destination": ["retrieval-17"]})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="match"):
        TrustBoundaryPolicy(
            sources={"spoof": _sources()["retrieval-17"]},
            protected_fields={"prepare_transfer": frozenset({"destination"})},
        )
