"""Synthetic enterprise and commerce action gateways with explicit provenance.

Two independent domain workflows share the same host-owned authority model.
The application host alone mints mandates, assigns source trust and installs
executors. No Salesforce, ServiceNow, CRM, payments, bank or model API is
contacted. This is proof of executable local veto, NOT a live vendor POC.

Run: python examples/enterprise_commerce_boundaries.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _provenance(
    *,
    action_field: str,
    resource_control: str = "trusted_operator",
    action_control: str | None = "trusted_operator",
    resource_data: str | None = None,
    action_data: str | None = None,
) -> ActionProvenance:
    controls = {"resource_id": frozenset({resource_control})}
    if action_control is not None:
        controls[action_field] = frozenset({action_control})
    data = {}
    if resource_data is not None:
        data["resource_id"] = frozenset({resource_data})
    if action_data is not None:
        data[action_field] = frozenset({action_data})
    return ActionProvenance(
        control_origins=controls,
        data_origins=data,
    )


def run_domain(
    *, domain: str, tool_name: str, effect: EffectType,
    action_field: str, action_value: str | int,
) -> dict:
    """Protect both the resource and the business action from untrusted control."""
    now = datetime.now(timezone.utc)
    journal: list[dict[str, str | int]] = []
    authorized = domain + "-resource-1"
    foreign = domain + "-resource-2"

    def executor(resource_id: str, **action: str | int) -> str:
        # In real deployments the downstream system must ALSO independently
        # enforce operator identity and resource scope.
        if action != {action_field: action_value}:
            raise ValueError("synthetic action is outside the host-installed contract")
        journal.append({"resource_id": resource_id, **action})
        return "synthetic-tool-returned"

    boundary = TrustBoundaryPolicy(
        sources={
            "trusted_operator": SourceLabel(
                "trusted_operator", SourceTrust.AUTHORITY, SourceChannel.OPERATOR_POLICY,
            ),
            "retrieved_doc": SourceLabel(
                "retrieved_doc", SourceTrust.DATA_ONLY, SourceChannel.RETRIEVAL,
            ),
            "peer_agent": SourceLabel(
                "peer_agent", SourceTrust.DATA_ONLY, SourceChannel.PEER_AGENT,
            ),
        },
        protected_fields={tool_name: frozenset({"resource_id", action_field})},
    )
    gateway = TrustedActionGateway(
        mandate=TaskMandate(
            mandate_id="synthetic-" + domain + "-mandate",
            principal_id="trusted-synthetic-enterprise-operator",
            agent_id="synthetic-" + domain + "-agent",
            run_id="synthetic-" + domain + "-run",
            expires_at=now + timedelta(minutes=5),
            allowed_tools=frozenset({tool_name}),
            allowed_resources=frozenset({authorized}),
            allowed_effects=frozenset({effect}),
        ),
        tools={tool_name: GatewayTool(
            ToolAuthority(effect, resource_argument="resource_id"), executor,
        )},
        trust_boundary=boundary,
    )
    denied: dict[str, str] = {}

    action_arguments = {"resource_id": authorized, action_field: action_value}
    for label, arguments, provenance in (
        ("retrieval", action_arguments, _provenance(
            action_field=action_field, resource_control="retrieved_doc",
        )),
        ("peer", action_arguments, _provenance(
            action_field=action_field, resource_control="peer_agent",
        )),
        ("contamination", action_arguments, _provenance(
            action_field=action_field, resource_data="retrieved_doc",
        )),
        ("resource_substitution", {**action_arguments, "resource_id": foreign},
         _provenance(action_field=action_field)),
        ("action_retrieval", action_arguments, _provenance(
            action_field=action_field, action_control="retrieved_doc",
        )),
        ("action_peer", action_arguments, _provenance(
            action_field=action_field, action_control="peer_agent",
        )),
        ("action_contamination", action_arguments, _provenance(
            action_field=action_field, action_data="retrieved_doc",
        )),
        ("action_missing_lineage", action_arguments, _provenance(
            action_field=action_field, action_control=None,
        )),
        ("action_omitted", {"resource_id": authorized},
         _provenance(action_field=action_field)),
    ):
        try:
            gateway.invoke(
                tool_name, arguments,
                action_id="attempt-" + label, now=now, provenance=provenance,
            )
        except AuthorizationDenied as exc:
            denied[label] = exc.verdict.reason
        else:
            raise AssertionError("unauthorized request executed: " + label)
        assert journal == [], "denied requests must never invoke the executor"

    allowed = gateway.invoke(
        tool_name, action_arguments,
        action_id="authorized", now=now,
        provenance=_provenance(action_field=action_field),
    )
    assert allowed.result == "synthetic-tool-returned"
    assert allowed.receipt.status is EffectStatus.PENDING
    assert not gateway.gate.effects_verified(["authorized"])
    assert journal == [action_arguments]
    return {
        "domain": domain,
        "executed": journal,
        "denied": denied,
        "receipt_status": allowed.receipt.status.value,
        "independently_verified": False,
    }


def run_demo() -> dict[str, dict]:
    return {
        "enterprise": run_domain(
            domain="enterprise", tool_name="update_crm_case", effect=EffectType.WRITE,
            action_field="case_status", action_value="resolved",
        ),
        "commerce": run_domain(
            domain="commerce", tool_name="request_refund",
            effect=EffectType.FINANCIAL_WRITE,
            action_field="amount_cents", action_value=2500,
        ),
    }


if __name__ == "__main__":
    for kind, report in run_demo().items():
        print(kind, "blocked:", sorted(report["denied"]),
              "authorized return:", report["receipt_status"],
              "(downstream effect not independently verified)")

