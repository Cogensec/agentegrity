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


def _provenance(*, controlled_by: str, contaminated_by: str | None = None) -> ActionProvenance:
    return ActionProvenance(
        control_origins={"resource_id": frozenset({controlled_by})},
        data_origins=(
            {"resource_id": frozenset({contaminated_by})}
            if contaminated_by else {}
        ),
    )


def run_domain(*, domain: str, tool_name: str, effect: EffectType) -> dict:
    """Fail closed on retrieval/peer control and resource substitution."""
    now = datetime.now(timezone.utc)
    journal: list[str] = []
    authorized = domain + "-resource-1"
    foreign = domain + "-resource-2"

    def executor(resource_id: str) -> str:
        # In real deployments the downstream system must ALSO independently
        # enforce operator identity and resource scope.
        journal.append(resource_id)
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
        protected_fields={tool_name: frozenset({"resource_id"})},
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

    for label, resource_id, provenance in (
        ("retrieval", authorized, _provenance(controlled_by="retrieved_doc")),
        ("peer", authorized, _provenance(controlled_by="peer_agent")),
        ("contamination", authorized, _provenance(
            controlled_by="trusted_operator", contaminated_by="retrieved_doc",
        )),
        ("resource_substitution", foreign, _provenance(controlled_by="trusted_operator")),
    ):
        try:
            gateway.invoke(
                tool_name, {"resource_id": resource_id},
                action_id="attempt-" + label, now=now, provenance=provenance,
            )
        except AuthorizationDenied as exc:
            denied[label] = exc.verdict.reason
        else:
            raise AssertionError("unauthorized request executed: " + label)
        assert journal == [], "denied requests must never invoke the executor"

    allowed = gateway.invoke(
        tool_name, {"resource_id": authorized},
        action_id="authorized", now=now,
        provenance=_provenance(controlled_by="trusted_operator"),
    )
    assert allowed.result == "synthetic-tool-returned"
    assert allowed.receipt.status is EffectStatus.PENDING
    assert not gateway.gate.effects_verified(["authorized"])
    assert journal == [authorized]
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
        ),
        "commerce": run_domain(
            domain="commerce", tool_name="request_refund",
            effect=EffectType.FINANCIAL_WRITE,
        ),
    }


if __name__ == "__main__":
    for kind, report in run_demo().items():
        print(kind, "blocked:", sorted(report["denied"]),
              "authorized return:", report["receipt_status"],
              "(downstream effect not independently verified)")
