# Late-arriving effect evidence (experimental)

This is the sixth implementation area in the Runtime Action Authority series.
The problem is **premature certainty**: a successful tool return is not proof
of a successful external side effect, and a timeout or network failure is not
proof of failure.

The Python OSS runtime now supports opt-in, process-local reconciliation of
delayed evidence with explicit uncertainty and conflicting outcomes.

## Meaning of receipt states

| State | Interpretation | Can a workflow count as verified success? |
| --- | --- | --- |
| `PENDING` | Tool execution started; no conclusive independent proof yet | No |
| `UNKNOWN` | Deadline elapsed or execution raised; side effect may or may not have occurred | No |
| `CONFIRMED` | A trusted host verifier validated independent downstream confirmation | Yes, if `independently_verified=True` |
| `FAILED` | A trusted host verifier validated independent downstream failure | No |
| `CONFLICTED` | Verified success and verified failure reports disagree | No; requires human investigation |

An `EffectReceipt` is **not a bank-signed proof**. Its status and reference
only reflect the host verifier's decision. Contradictory verified observations
are never silently last-write-wins: `CONFLICTED` is sticky until external
resolution. The SDK deliberately does not implement a conflict-clear API.

## Host integration

```python
from datetime import datetime, timedelta, timezone
from agentegrity.core.action_security import (
    ActionSecurityGate, EffectReceipt, EffectStatus,
    EffectType, TaskMandate, ToolAuthority,
)

mandate = TaskMandate(
    mandate_id="synthetic-mandate", principal_id="synthetic-customer",
    agent_id="bank-agent", run_id="bank-run",
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    allowed_tools=frozenset({"prepare_transfer"}),
    allowed_resources=frozenset({"account-42"}),
    allowed_effects=frozenset({EffectType.FINANCIAL_WRITE}),
)
gate = ActionSecurityGate(
    mandate,
    {"prepare_transfer": ToolAuthority(
        EffectType.FINANCIAL_WRITE, resource_argument="account_id"
    )},
    evidence_timeout=timedelta(minutes=5),
)

execution = gate.invoke(
    agent_id="bank-agent", run_id="bank-run",
    tool_name="prepare_transfer",
    arguments={"account_id": "account-42"},
    executor=lambda account_id: "synthetic-request-dispatched",
)
action_id = execution.verdict.intent.action_id

# A returned result still does not prove the external write.
assert gate.effects_verified([action_id]) is False

# The application must schedule/poll this method; there is no background timer.
gate.expire_unresolved_effects()

# After independently checking a downstream record, reconcile it.
# This verifier is illustrative only. In production, perform an authenticated
# lookup using action_id, downstream idempotency key, principal, resource,
# operation type and committed effect, not mere reference-string equality.
evidence = EffectReceipt(
    action_id=action_id,
    status=EffectStatus.CONFIRMED,
    evidence_source="synthetic-ledger",
    evidence_reference="synthetic-entry-001",
)
verified = gate.record_effect(
    evidence,
    verifier=lambda claim: check_authoritative_ledger(claim),  # implement in host
)
if verified.status is EffectStatus.CONFIRMED and gate.effects_verified([action_id]):
    print("Host-verified completed effect")
```

The host must implement `check_authoritative_ledger` in the example. Do not
use a verifier that simply returns `True`, checks model-provided messages,
trusts a claimed `independently_verified` flag, or only checks whether a
reference string exists.

### Timeouts

With `evidence_timeout=timedelta(...)`, the trusted host calls
`gate.expire_unresolved_effects()` periodically. Once an execution's evidence
deadline passes, `PENDING` changes to `UNKNOWN`. The sweep is idempotent.
It cannot change independently verified `CONFIRMED` or `FAILED` records,
and cannot mark an effect failed. `evidence_timeout=None` retains the
original behavior: effects stay pending without an explicit uncertain outcome.

Only actual `invoke()` or `ainvoke()` creates a pending receipt; pre-tool
authorization checks and observational framework hooks do not.

### Delayed and out-of-order evidence

A trusted, independently verified report can later resolve `UNKNOWN` to
`CONFIRMED` or `FAILED`. A verified opposite result after a previous
verified outcome moves the receipt to `CONFLICTED` and blocks
`effects_verified()`. An identical verified source/reference/outcome may be
replayed without another transition. Reports must be correlated to the exact
action. Unverified success/failure claims do not become authoritative.

The process-local `gate.effect_observations` returns an immutable snapshot of
the receipt transition metadata (timestamp, status, reason, source and opaque
reference). It is not a cryptographic attestation or durable evidence store.
A restart discards these observations and every unresolved receipt.

`effects_verified(action_ids)` returns true only if every requested action
is independently verified as `CONFIRMED`. An empty set is deliberately
treated as *not verified*. Use this helper to guard **dependent or irreversible
operations**, but still enforce permissions in the actual downstream service.

## Limits and security responsibilities

- **Process-local only**: no durable reconciliation queue, restart recovery,
  event signatures, secure cross-process attestation, or persistent dedupe.
  Those require integration with a trustworthy external event/evidence store.
- The application owns the verifier and must ensure that a report's source,
  action ID, run ID, effect, resource, principal and external idempotency
  identity actually match a committed downstream operation. A callback
  returning true is not itself evidence of independent verification.
- Receipt/source references may be sensitive identifiers; keep them opaque
  and handle according to deployment retention/privacy policy.
- Contradictory trusted reports are *ambiguous*. This SDK never picks a
  winner or automatically retries an uncertain irreversible action.
  Reconciliation and compensation belong to the downstream domain.
- `now=` parameters are for trusted host clocks and deterministic tests only.
  Never derive timestamps, callback verifiers or purported evidence metadata
  from untrusted LLM/tool content.
- A successful local executor may have hidden side effects. A failure may
  occur after remote commitment. No code here can infer that causal truth.

All tests use synthetic, offline evidence and banking-agent actions.
