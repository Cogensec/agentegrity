# Provenance-aware trust boundaries (experimental)

This opt-in feature extends Agentegrity's task-scoped **Runtime Action Authority**
with two explicit defenses:

1. **Unclear trust boundaries:** a verified task request or operator policy is
   the only source that can authorize protected tool arguments. Retrieved pages,
   MCP/tool responses, memory, and peer-agent messages are **data only**, even if
   their contents claim to be system messages or security policies.
2. **Data-as-control:** external data can be used in an ordinary payload, but
   not promoted into a protected parameter such as a transfer destination,
   approval identity, target account, or credential selector.

**This is not an automatic prompt-injection detector.** The SDK cannot
reliably deduce source-to-action causality from model text. The application must
capture and supply *accurate provenance* outside model control; false, missing
or partial lineage undermines the guarantee. The host's own authorization
and the downstream service's resource permissions remain authoritative.

## Integration: synthetic banking example

```python
from datetime import datetime, timedelta, timezone
from agentegrity import (
    ActionProvenance,
    ActionSecurityGate,
    EffectType,
    SourceChannel,
    SourceLabel,
    SourceTrust,
    TaskMandate,
    ToolAuthority,
    TrustBoundaryPolicy,
)

mandate = TaskMandate(
    mandate_id="test-mandate", principal_id="verified-customer",
    agent_id="synthetic-agent", run_id="synthetic-run",
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    allowed_tools=frozenset({"prepare_transfer"}),
    allowed_resources=frozenset({"account-42"}),
    allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
)
boundary = TrustBoundaryPolicy(
    # These labels are created by an authenticated host controller, not an LLM.
    sources={
        "customer-task": SourceLabel(
            "customer-task", SourceTrust.AUTHORITY, SourceChannel.VERIFIED_USER_REQUEST
        ),
        "retrieved-invoice": SourceLabel(
            "retrieved-invoice", SourceTrust.DATA_ONLY, SourceChannel.RETRIEVAL
        ),
    },
    protected_fields={
        "prepare_transfer": frozenset({"account_id", "destination"}),
    },
)
gate = ActionSecurityGate(
    mandate,
    {"prepare_transfer": ToolAuthority(
        EffectType.FINANCIAL_WRITE, resource_argument="account_id"
    )},
    trust_boundary=boundary,
)
provenance = ActionProvenance(
    control_origins={
        "account_id": frozenset({"customer-task"}),
        "destination": frozenset({"customer-task"}),
    },
    # An invoice may be read as data without being able to change a transfer.
    data_origins={"notes": frozenset({"retrieved-invoice"})},
)
result = gate.invoke(
    agent_id="synthetic-agent", run_id="synthetic-run",
    tool_name="prepare_transfer",
    arguments={
        "account_id": "account-42",
        "destination": "approved-payee",
        "notes": "synthetic invoice",
    },
    provenance=provenance,
    executor=lambda account_id, destination, notes: "synthetic-prepared",
)
assert result.receipt.assurance == "unverified"
```

## Adapter hooks

Set `action_security=gate`, `enforce=True` and optionally
`provenance_provider=host_lineage_lookup` when constructing a framework adapter.
The provider is an application-installed synchronous callback receiving the
tool name and arguments and returning `ActionProvenance`. It must consult
**host-owned lineage**, for example immutable per-run provenance records
attached to verified messages and tool-output boundaries. It must **not**
trust self-reported `tool_input["provenance"]`, retrieved annotations, an
untrusted agent trace, or other attacker-controllable fields.

When the boundary requires lineage and no provider exists, the hook **denies**
rather than silently allowing. When provider evaluation raises an exception,
the existing enforced pre-tool failure path denies.

Pre-tool hooks are *not* guaranteed execution fences in all frameworks:
some adapters only observe. For consequential actions, use
`gate.invoke(..., provenance=...)` or `await gate.ainvoke(..., provenance=...)`
at the **real execution boundary**, and enforce actual privileges in the tool
service. A hook decision does not create an effect receipt; only execution
attempts do.

## Fail-closed semantics

- Every tool in a boundary-enabled gate needs an explicit entry in
  `protected_fields`. Unknown policy entries are denied even if otherwise
  allowed by the task mandate.
- Each protected argument must be supplied, and all control sources for it
  must be present and authorized; unknown or mixed-trust origins deny.
- Data-only source material in a protected argument denies, including when
  mixed with a trusted source (`data_promoted_to_control`).
- Explicit empty protected-argument sets are allowed for a *reviewed tool* that
  genuinely has no high-impact arguments. This is not a general exemption
  for arbitrary read-labeled tools; hidden effects require independent
  validation.
- Source identifiers are only meaningful within an authenticated host registry.
  This implementation does **not** authenticate data lineage cryptographically.
- Policy snapshots and provenance mappings are immutable in process; this
  is not durable cross-process evidence or adversarial runtime isolation.
- Downstream effect receipts remain pending until independently reconciled.

## Limits / follow-on work

This feature cannot detect prompt injection when the application does not
track influence, when a purportedly trusted user request is spoofed before
entering the host, or when a tool performs hidden undeclared effects. It
does not implement automatic semantic taint propagation, immutable source
attestations, cryptographically backed provenance, safe agent delegation,
or cross-agent evidence completeness. Those require host-framework
instrumentation and service-side enforcement.

The tests use synthetic banking actions only; they do not touch real accounts
or networks.
