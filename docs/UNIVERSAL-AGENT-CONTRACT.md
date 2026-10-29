# Universal Agent Event Contract (v1)

**Status:** Opt-in code on the feature branch. The current exporter protocol is
unchanged. This is the U1 foundation in the
[Agent Ecosystem Integration Roadmap](AGENT-ECOSYSTEM-ROADMAP.md), **not**
a claim of new host integrations or enforcement coverage.

## Contract and threat boundary

The canonical event records **metadata about an agent action or lifecycle
observation**, without prompts, tool arguments, tool outputs, tokens, customer
records, or other arbitrary content. New integrations should derive the
fields from a trusted host-side callback, not from an agent-generated message.

- JSON schema: [canonical event](../schemas/agent-event-v1.json)
  and [capability card](../schemas/agent-capabilities-v1.json).
- Python: `agentegrity.core.agent_events` (also exported from the package root).
- TypeScript: `@agentegrity/client` exports types and runtime parsers.
- Both runtimes test the
  [same positive and negative examples](../tests/fixtures/agent_event_contract_v1.json).
- Existing `FrameworkEvent` exporter and TypeScript reporter messages are
  **not** silently reinterpreted as authenticated canonical events.

**Source assurance is a claim, not proof.** `unverified` means no source
authentication claimed, `host_asserted` means the trusted integrator
attributes the event to its host, and `authenticated` requires a nonempty
`evidence_ref`. The parser does **not** authenticate signatures, evaluate
references, or distinguish a malicious sender from a legitimate host.
Receiving services must independently verify keys, tokens, issuer and tenant
bindings before upgrading a claim to trusted evidence.

IDs (`event_id`, `agent_id`, `run_id`, `action_id`) are opaque
correlation fields and are not authority grants. `occurred_at` requires an
explicit timezone. `event_type` is namespaced dot notation (for example,
`tool.intent`, `tool.result`, `agent.delegated`, `policy.verdict`).
Unknown valid event types are allowed for forward-compatible observation,
but consumers must not infer authorization semantics from them.

## Python example

```python
from datetime import datetime, timezone

from agentegrity import CanonicalAgentEvent, EventSource

event = CanonicalAgentEvent(
    event_id="event-001",
    event_type="tool.intent",
    occurred_at=datetime.now(timezone.utc),
    agent_id="agent-001",
    run_id="run-001",
    source=EventSource(host="customer-owned-gateway", integration="python-sdk"),
    action_id="action-001",
    tool_name="issue_refund",
    effect="financial_write",
)
wire = event.to_dict()  # JSON-compatible metadata, with no raw arguments
reparsed = CanonicalAgentEvent.from_dict(wire)
assert reparsed.to_dict() == wire
```

## Capability declarations

Each integration must declare *all five* levels, including unavailable ones:
`observe`, `evaluate`, `authorize`, `verify_effect`, `recover`.

| Status | Meaning |
| --- | --- |
| `unavailable` | No available or verified capability at this host boundary |
| `implemented` | An integration implements the path; effect not proven by the declaration |
| `validated` | Integrator supplies a reference to host/version-specific validation evidence |

Even a `validated` claim and its test path are **not independent
certification**. A receiving service must inspect the referenced test result,
confirm the host/version, and check its own enforcement or verification boundary.

An event listener is not a tool-execution gate. A tool-return receipt is not
an independently verified downstream effect. A checkpoint callback does not
prove restoration of external business state. Agentegrity's
[Runtime Action Authority](RUNTIME-ACTION-AUTHORITY.md) is a distinct opt-in
execution-boundary control.

## Rollout and privacy

1. Keep existing SDK/exporter behavior stable: consumers must opt into v1
   canonical events explicitly.
2. For each adapter, capture its own host-versioned capability card and
   negative test for denied effects before asserting authorization.
3. Translate legacy events only when a host-side adapter can prove where
   agent/run/action identifiers originated. Mark incomplete source assurance
   as `unverified`; do not guess missing fields.
4. For Pro ingest, authenticate tenant and sender independently of
   self-reported `source.assurance`, enforce payload size bounds and event
   replay protection, and persist only permitted metadata. **Not yet shipped**.
5. Keep physical/robotics integrations simulation-only until external safety
   controllers and independent safety review exist.

This contract deliberately has no raw `data` or `metadata` bag. A future
content-capture extension must explicitly specify consent, minimization,
redaction, size limits, retention and destination controls before implementation.
