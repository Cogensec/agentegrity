# `@agentegrity/client`

TypeScript client for [agentegrity](https://github.com/cogensec/agentegrity). Emit agent events from a **Node / Bun / browser** agent to any backend that implements the [Agentegrity Exporter HTTP API](../../schemas/openapi.yaml).

The Python OSS library ships the scoring evaluator, attestation chain, and 5 framework adapters. This client lets non-Python agents emit the same event stream those adapters produce, targeted at the same HTTP contract the commercial `agentegrity-pro` dashboard listens on.

## Install

Not yet published to npm. For now, vendor the `clients/typescript/` directory or install from source:

```bash
cd clients/typescript && npm install && npm run build
```

## Use

```ts
import { AgentegrityReporter } from "@agentegrity/client";

const reporter = new AgentegrityReporter({
  baseUrl: "http://localhost:8787",
  profile: {
    agent_id: "my-agent",
    name: "my-agent",
    agent_type: "tool_using",
    capabilities: ["tool_use"],
    deployment_context: "cloud",
    risk_tier: "medium",
  },
});

await reporter.start();
await reporter.emit({ event_type: "pre_tool_use", data: { tool_name: "search" } });
await reporter.emit({ event_type: "post_tool_use", data: { tool_name: "search" } });
await reporter.end({ events: 2 });
```

Fail-open: network / serialization errors are caught and routed to `onError` (default `console.warn`), never thrown.

## Contract

Wire format is defined by:
- [`schemas/openapi.yaml`](../../schemas/openapi.yaml) — the three HTTP endpoints
- [`schemas/exporter/*.json`](../../schemas/exporter/) — JSON Schemas for request bodies

Any backend (the commercial `agentegrity-pro`, your own FastAPI sidecar, anything) that honors those three endpoints accepts events from this client.

## Example

See [`examples/basic.ts`](examples/basic.ts) for a full Bun-compatible walkthrough.
