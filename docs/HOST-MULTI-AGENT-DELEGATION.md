# Host-controlled multi-agent delegation

**Status:** An opt-in, process-local delegation convenience layer over the
existing `ActionSecurityGate.delegate` in OSS. This PR is based on
[OSS #111](https://github.com/Cogensec/agentegrity/pull/111), the trusted
execution gateway. **Merge #111 first**, then review the dependent diff.

## Motivation

A supervisor agent can have broad authority, but its subagents should never
inherit that authority automatically. Example: a financial workflow supervisor
can read account data and prepare payments; a document-analysis worker gets
read-only access to one approved document.

```python
child = parent_gateway.delegate(
    agent_id="document-reader",
    run_id="reader-run",
    allowed_tools=frozenset({"read_document"}),
    allowed_resources=frozenset({"document-1"}),
    allowed_effects=frozenset({EffectType.READ}),
    expires_at=operator_approved_expiration,
)
result = child.invoke(
    "read_document", {"document_id": "document-1"},
)
```

The trusted host configures `max_delegation_depth=1` (default **0**, disabled)
when it constructs the root gateway. The child is created by the existing core
gate, which validates that:
- child tools, resources, effects and credentials are subsets of the parent;
- the child cannot impersonate any ancestor agent ID or run ID;
- expiry, not-before and credential expiry cannot expand parent lifetimes;
- the same exact tool authority declarations and provenance boundary apply;
- ancestor revocation denies child and grandchild execution before the
  registered callable runs;
- an authorized callable return creates a pending receipt, **not an
  independently verified downstream effect**.

The wrapper copies **only child-granted executor registrations**. Other
executors cannot be recovered by supplying their names to the child gateway.
No model-generated message, peer output, MCP tool description, or retrieved
content may mint or modify an actual `TaskMandate`.

## Verify

```bash
python -m pytest tests/test_delegated_gateway.py -v
python examples/delegated_customer_workflow.py
```

Tests cover a financial coordinator, document-analysis and communications
workers, nested delegation, depth limits, credentials/scope attenuation,
host-controlled provenance, ancestor revocation and async execution.
All effects are synthetic. There is **no cross-process trust propagation,
durable global revocation registry or inter-service agent authentication**.
Child calls are blocked only while their host uses this gateway. Remote
services must still enforce narrowly scoped credentials and independent
idempotency. This does not make ordinary observer hooks enforcement points.

## Future work

For distributed agent topologies, bind grants to authenticated peer
identities and delegated workload tokens, reconcile revocations through a
durable policy control plane, and verify receipts against authoritative
external systems. Until then, this is a trustworthy in-process host execution
boundary, not fleet-wide delegated authority.
