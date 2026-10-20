# Cascading delegation: bounded subagent authority (experimental)

This change builds on the merged **Runtime Action Authority** and
**Provenance-aware Trust Boundaries** work. Subagents should not gain all of
the supervisor's standing privileges or grant themselves wider authority
through prompts, peer messages, or model-generated handoff metadata.

## Trust model

- A **trusted host controller** owns an `ActionSecurityGate` with an explicit
  run-bound `TaskMandate`. The controller, not a subagent, calls `gate.delegate()`.
- Delegation is **disabled by default** (`max_delegation_depth=0`). A host
  may enable a bounded chain, up to eight levels.
- Delegated grants must be literal subsets of the parent's allowed tools,
  resources, effects and credentials; never an expansion. Child identity and
  run ID must differ from the parent's. Principal identity is inherited.
- Child expiry must be inside its parent's lifetime. `not_before` and
  credential expiry cannot exceed their parent's authority window.
- Each child inherits the trusted tool declarations and
  `TrustBoundaryPolicy` from its parent. It cannot opt out of provenance
  requirements by delegating.
- Each gate maintains a parent reference and checks ancestors for expiry
  and revocation on every decision and immediately before starting a tool
  invocation. A revoked parent denies its entire in-process descendant tree.
- Delegation scope and revocation are **process-local**. Mandate IDs and
  `delegation_lineage` are diagnostic metadata, not cryptographic proofs.
  Do not serialize a mandate and treat it as an authoritative external token.

## Synthetic banking example

```python
from datetime import datetime, timedelta, timezone
from agentegrity.core.action_security import (
    ActionSecurityGate, EffectType, TaskMandate, ToolAuthority,
)

now = datetime.now(timezone.utc)
root = ActionSecurityGate(
    TaskMandate(
        mandate_id="supervisor-task",
        principal_id="verified-customer",
        agent_id="supervisor-agent",
        run_id="supervisor-run",
        expires_at=now + timedelta(minutes=30),
        allowed_tools=frozenset({"read_account", "prepare_transfer"}),
        allowed_resources=frozenset({"account-42"}),
        allowed_effects=frozenset({EffectType.READ, EffectType.FINANCIAL_WRITE}),
    ),
    {
        "read_account": ToolAuthority(EffectType.READ, resource_argument="account_id"),
        "prepare_transfer": ToolAuthority(
            EffectType.FINANCIAL_WRITE, resource_argument="account_id"
        ),
    },
    max_delegation_depth=2,
)

# Trusted controller attenuates permissions before starting the worker.
worker = root.delegate(
    agent_id="read-only-worker",
    run_id="worker-run",
    allowed_tools=frozenset({"read_account"}),
    allowed_resources=frozenset({"account-42"}),
    allowed_effects=frozenset({EffectType.READ}),
    expires_at=now + timedelta(minutes=5),
)
result = worker.invoke(
    agent_id="read-only-worker",
    run_id="worker-run",
    tool_name="read_account",
    arguments={"account_id": "account-42"},
    executor=lambda account_id: {"account_id": account_id, "synthetic": True},
)
assert result.receipt.assurance == "unverified"

# Both the worker and any grandchildren are now revoked.
root.revoke()
```

The worker gate can be provided as the `action_security` argument to an
enforcing framework adapter configured with `enforce=True`. The existing
adapter automatically uses the worker's `TaskMandate.run_id` as its session ID.
A `subagent_start` event only records a lifecycle event: it does **not**
automatically grant or expand the subagent's authority.

## Important limits

**This is not a distributed identity or revocation service.** A separate
process must receive independently verifiable, attenuated credentials from an
actual trusted identity/service authority. Simply copying `TaskMandate` into
another process does not preserve parent revocation, lineage binding, or
provenance control. A compromised runtime that can instantiate arbitrary gates
or invoke tools outside the wrapper is outside this protection model.

The process-local gate checks revocation and expiry before effect invocation.
A tool may still execute for some time after revocation if it has already
started. Tools need downstream cancellation, credential revocation and
independent effect reconciliation. `max_delegation_depth` is a host-side
configuration setting, not a model-specified capability.

`now=` exists for deterministic tests; never derive the trusted clock from
untrusted model arguments. The `delegate()` API is host-only and should
not be published as a subagent-executable tool.

## Regression coverage

Synthetic tests cover nested scoping, permission escalation attempts,
distinct run/agent identity, credentials, expiry, denied tool execution,
revocation after authorization but before execution, inherited provenance,
adapter enforcement, and lifecycle-event spoofing.

This PR addresses **cascading delegation** as an in-process opt-in primitive.
It does not yet implement the sixth issue, **late-arriving evidence**.
