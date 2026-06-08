# @agentegrity/google-adk

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for the **Google Agent Development Kit (ADK)** JS. Mirrors the Python `agentegrity.google_adk` module 1:1.

## Install

```bash
npm i @agentegrity/google-adk
```

## Use

```ts
import { Agent, Runner } from "@google/adk";
import { instrument, report } from "@agentegrity/google-adk";

const agent = new Agent({ name: "my-agent" });
instrument(agent);
const runner = new Runner(agent);
await runner.run({ input: "hello" });
console.log(await report());
```

The adapter duck-types the ADK callback API (`addBeforeAgentCallback`, `addBeforeToolCallback`, plugin registration, and `EventEmitter`-style `on()`) so it works across ADK JS 0.x versions without pinning a specific release.

`instrument()` returns a cleanup function — call it on shutdown to fire the session-end event:

```ts
const close = instrument(agent);
// ... agent runs ...
await close();
```

## API

| Function | Returns |
|---|---|
| `instrument(agent, options?)` | Cleanup function `() => Promise<void>` |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |

## License

Apache-2.0
