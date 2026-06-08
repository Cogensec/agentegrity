# @agentegrity/crewai

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for **CrewAI JS**. Mirrors the Python `agentegrity.crewai` module 1:1.

> **Note:** CrewAI JS is pre-1.0 and its event API has been evolving. This package accepts any of the three patterns below; if you're using a version that emits differently, wire events manually via `bridge.onEvent(name, payload)`.

## Install

```bash
npm i @agentegrity/crewai
```

## Use

### Pattern A — attach to a crew event emitter

```ts
import { instrument, report } from "@agentegrity/crewai";

const bridge = instrument();
bridge.attach(crew.events);     // crew.events is a Node EventEmitter
await crew.kickoff();
console.log(await report());
```

### Pattern B — pass handlers via `callbacks: [...]`

```ts
import { instrument, report } from "@agentegrity/crewai";

const bridge = instrument();
await crew.kickoff({ callbacks: [bridge.handlers] });
console.log(await report());
```

### Pattern C — manual event forwarding

```ts
import { instrument, report } from "@agentegrity/crewai";

const bridge = instrument();
await bridge.onEvent("crew.kickoff", { goal: "..." });
// ... your crew runs ...
await bridge.onEvent("crew.finish", { output });
console.log(await report());
```

## Event mapping

CrewAI event → agentegrity event:

| CrewAI | agentegrity |
|---|---|
| `crew.kickoff`, `crew.start`, `agent.start` | `user_prompt_submit` |
| `tool.start` | `pre_tool_use` |
| `tool.end` | `post_tool_use` |
| `tool.error` | `post_tool_use_failure` |
| `crew.finish`, `crew.end`, `agent.finish` | `stop` |

Unknown events are silently ignored.

## API

| Function | Returns |
|---|---|
| `instrument(options?)` | `CrewAIEventBridge` (with `attach`, `onEvent`, `handlers`, `close`) |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |

## License

Apache-2.0
