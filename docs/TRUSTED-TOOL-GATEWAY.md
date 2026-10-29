# Trusted Tool Gateway (opt-in)

The `TrustedActionGateway` provides a host-controlled way to call
`ActionSecurityGate.invoke()` / `ainvoke()` for **custom agents, financial
agents, enterprise workflows and other customer-owned tools**. This is the
U2 execution-boundary workstream in
[Agent Ecosystem Integration Roadmap](AGENT-ECOSYSTEM-ROADMAP.md).

## Integration boundary

1. The **trusted application host**, not the model or an MCP description,
   constructs a run-bound `TaskMandate` and registers named `GatewayTool`
   executors with `ToolAuthority` declarations.
2. A tool call passes a bounded, isolated JSON argument snapshot to the
   existing action gate and, only after a successful authorization check,
   the **same snapshot** to the executor. The gateway cannot prevent effects
   if callers can bypass it to reach the service directly.
3. The gateway binds `agent_id` and `run_id` from the trusted mandate,
   rather than reading purported identity from tool arguments.
4. Unauthorized first actions never invoke the registered function.
   Duplicate action IDs are rejected and cannot replay a committed call.
5. A successful executor return creates a **PENDING** effect receipt,
   never an independently verified payment, refund or account modification.
   Exceptions may mean a downstream effect occurred; the receipt becomes
   `UNKNOWN`. Only a trusted host verifier querying an authoritative
   external system can confirm or fail an effect.
6. Use `gateway.gate.record_effect(..., verifier=...)` only in the trusted
   host. The SDK cannot authenticate remote service identities on its own.

## Synthetic example

```bash
python examples/trusted_tool_gateway.py
python -m pytest tests/test_trusted_tool_gateway.py -v
```

The example never contacts a bank or CRM. The tests prove authorization
denial at the **local Python callable boundary**. They do not prove a cloud
provider's hook coverage or remote service enforcement.

## Operational limitations

- **Opt-in:** Existing adapters, exporters and hosted agents are unchanged.
- **Host-owned:** Do not expose mandate construction, executor registry,
  trusted provenance, clock control or receipt verification to agent messages.
- **JSON inputs only:** 64 KiB serialized argument limit; rejected values
  never reach executors. Tool arguments are not exported by this gateway.
- **Process-local:** Mandates, replay tracking and receipts are not durable
  cross-process capabilities. A process restart loses this state.
- **Downstream authorization remains mandatory:** The actual bank, CRM,
  file system or cloud API must enforce independently scoped credentials,
  idempotency and service-side authorization.
- **No automatic rollback:** A timed-out payment may already have settled.
  Reconciliation and business compensation require domain-specific host code.
- **No physical-safety claim:** This gateway is not an actuator interlock or
  certified safety controller.

For heterogeneous platforms, add host-specific adapters **after** testing
whether their actual tool execution can be intercepted. An observation-only
hook must not be described as an enforced gateway.
