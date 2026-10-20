# Runtime Action Authority (experimental, Python OSS)

This opt-in capability is available in the current repository source,
merged after the v0.12.0 release. It targets **standing access** and
**first-call effects**. Consult the version installed in your environment
before assuming these APIs are available.
The `ActionSecurityGate` is an application-installed, explicit allowlist that
compares each action to a time-limited task mandate and a trusted tool/effect
registry. It does not infer authorization from prompt instructions, MCP
metadata, retrieved data, or agent reasoning.

## Trust model

- Construct `TaskMandate` and `ToolAuthority` in a trusted control plane, not
  from agent-supplied instructions. Each known tool must declare one effect
  type and how to resolve an exact resource ID. Unknown tools deny.
- `check()` returns an authorization decision **without** opening a pending
  effect receipt. Only an actual `invoke()` or `ainvoke()` execution attempt
  creates one, avoiding phantom effects when another governance rule denies.
  Hooks are **not**
  a universally reliable enforcement boundary: a particular framework can
  swallow or ignore a denial. `enforce=True` is required when configuring the
  adapter with `action_security=`; separately verify your host's hook semantics.
- `invoke()` is the synchronous execution-boundary wrapper; `ainvoke()`
  awaits async executors and observes their failures/cancellation. Both enforce
  before execution and pass precisely the checked arguments. An async executor
  passed to `invoke()` is rejected before execution, and an unexpected awaitable
  result fails closed with an **unknown** effect.
  It is not a sandbox; the callable or a downstream service can still have
  undeclared side effects. Use service-side resource policy for assurance.
- Successful tool return creates a **pending**, not verified, effect receipt.
  Exceptions create an **unknown** effect because remote commits can occur
  before the error is received. Only an application-owned verifier checking
  independent system evidence can upgrade assurance. `verifier` callbacks are
  trusted application code, not a cryptographic proof by themselves.
- No credential values or raw tool arguments are stored in gate decisions;
  opaque resource and credential references may still be sensitive and should
  be privacy-reviewed before exporting. State is process-local, not durable.

## Example: synthetic banking action

```python
from datetime import datetime, timedelta, timezone
from agentegrity.core.action_security import (
    ActionSecurityGate, EffectType, TaskMandate, ToolAuthority,
)

agent_id = "synthetic-bank-agent"
mandate = TaskMandate(
    mandate_id="task-001", principal_id="customer-001", agent_id=agent_id,
    run_id="synthetic-run-001",
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    allowed_tools=frozenset({"read_account"}),
    allowed_resources=frozenset({"account-42"}),
    allowed_effects=frozenset({EffectType.READ}),
)
gate = ActionSecurityGate(mandate, {
    "read_account": ToolAuthority(EffectType.READ, resource_argument="account_id"),
    "prepare_transfer": ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="account_id"),
})

# The host's real function is never called when a tool is not authorized.
read = gate.invoke(
    agent_id=agent_id, run_id="synthetic-run-001", tool_name="read_account",
    arguments={"account_id": "account-42"},
    executor=lambda account_id: {"account_id": account_id, "balance": "synthetic"},
)
assert read.receipt.assurance == "unverified"
```

An adapter can be constructed with `action_security=gate` and `enforce=True`.
When used in a framework adapter, construct the mandate with the same
`profile.agent_id`. A bound `TaskMandate.run_id` automatically becomes the
adapter session ID. Alternatively, pass an explicit matching `session_id=`
to the adapter constructor; a mismatch raises immediately. Pre-tool hooks do
**not** create effect receipts: use `invoke()` or `ainvoke()` at the trusted
execution boundary and independently verify downstream effects.

## Scope and follow-on work

This page describes the core task-scoped execution gate. Additional,
still-experimental Python OSS capabilities now include
[provenance-aware trust boundaries](TRUST-BOUNDARIES-AND-DATA-CONTROL.md),
[bounded in-process delegation](CASCADING-DELEGATION-AUTHORITY.md), and
[late-arriving evidence reconciliation](LATE-ARRIVING-EFFECT-EVIDENCE.md).

These do **not** supply identity-provider-issued credentials, automatic
source-to-instruction taint propagation, durable replay protection,
cross-process revocation, or independently verified downstream effects.
Those require authenticated infrastructure outside this library. Do not
market this as end-to-end containment or production-wide automatic blocking.
