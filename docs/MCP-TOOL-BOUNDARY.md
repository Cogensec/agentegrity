# MCP tool execution boundary (U2/U4)

**Status:** Opt-in Python tool dispatcher, pinned to the trusted gateway
introduced by [PR #111](https://github.com/Cogensec/agentegrity/pull/111).
This PR is **stacked on #111** and must be rebased once #111 merges. This is
not a general-purpose MCP server, client, HTTP proxy or hosted-agent hook.

The purpose is to protect **tool calls at an actual host-controlled execution
boundary**, irrespective of which agent or model originated the request.
Examples include payment/refund assistants, SaaS customer-support workflows,
enterprise agents and research agents using MCP tools.

## Security model

The host registers the actual executor and its `ToolAuthority` under
`TrustedActionGateway`, and issues a run-bound `TaskMandate` from a trusted
control plane. The `MCPToolBoundary` accepts the MCP `tools/call` **params**
object, not a JSON-RPC envelope:

```json
{"name":"issue_refund","arguments":{"invoice_id":"invoice-42"}}
```

`await boundary.call_tool(params, action_id=host_action_id, provenance=host_lineage)`
performs permission checks **before** invoking the trusted registered tool.
Denied calls raise `AuthorizationDenied`; your host's MCP SDK must convert
that into its protocol-specific tool error without exposing sensitive policy
details. A successful call returns an `ActionExecution` containing the
executor's native result and **PENDING** receipt, which your MCP server
serializes through its own SDK. The returned receipt is not proof of refund,
payment settlement, external writes, or completion.

Do **not** derive mandates, identity, tools, effect classifications,
permission scopes, clock overrides, source trust or `ActionProvenance`
from model prompts, MCP tool descriptions, tool outputs, retrieved pages or
the JSON-RPC request. An MCP request ID is not a host-side action idempotency
key. The host must mint/bind `action_id` and authenticate any provenance
before calling `call_tool`. To prevent bypass, every consequential tool
dispatch path must pass through the boundary, and downstream services
must enforce their own credentials and authorization.

The bridge purposely rejects unknown parameters (including `_meta`) rather
than silently accepting them as authority. It accepts only `name` and
object `arguments`; an absent arguments object becomes `{}` and any
invalid/oversized arguments are rejected by the trusted gateway.

## Simulated integration test

```bash
python examples/mcp_authorized_customer_agent.py
python -m pytest tests/test_mcp_tool_boundary.py -v
```

The tests implement a synthetic payment/refund tool and CRM plan update,
with both authorized and denied paths. They exercise:
- resource scope and mandate expiry;
- replay of a trusted host action ID;
- untrusted MCP tool/retrieval data promoted into protected parameters;
- missing trusted provenance for payment and plan changes;
- protected parameter authorization for legitimate requests; and
- malformed, overprivileged or unknown `tools/call` inputs.

These tests exercise *our application dispatch functions*, **not** a live
MCP SDK's server routes or all MCP clients. MCP SDK v2 uses a different
server API than v1; a concrete host adapter will require pinned SDK/transport
conformance tests and documented cancellation/failure semantics before the
project can claim version-specific MCP support.

## Limitations

- No universal interception of an existing third-party MCP host.
- No distributed replay ledger, centralized policy management, remote
  source authentication or service-side idempotency.
- A host can still bypass the gateway if it invokes its original tool
  callable directly; enforce mediation in its actual dispatch architecture.
- Downstream effects are not verified without independent reconciliation,
  and recovering a Python run does not restore external business state.
- No raw prompts, MCP tool descriptions or customer data are exported by
  the bridge; executors retain responsibility for safe handling of
  their own arguments and results.

For official Python MCP SDK API changes, follow the
[MCP SDK documentation](https://py.sdk.modelcontextprotocol.io/)
rather than assuming compatibility with one server implementation.
