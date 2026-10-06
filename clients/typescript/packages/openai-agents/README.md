# @agentegrity/openai-agents

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for the **OpenAI Agents JS SDK** (`@openai/agents`). Mirrors the Python `agentegrity.openai_agents` module 1:1.

## Install

```bash
npm i @agentegrity/openai-agents @openai/agents
# or: bun add @agentegrity/openai-agents @openai/agents
```

## Use

```ts
import { Agent, Runner } from "@openai/agents";
import { instrument, report } from "@agentegrity/openai-agents";

const runner = instrument(new Runner());
await runner.run(agent, "hello");
console.log(await report());
```

The adapter listens on the runner's lifecycle events (`agent_start`,
`agent_tool_start`, `agent_tool_end`, `agent_handoff`, `agent_end`). The
SDK's `run()` helper uses an internal runner, so create a `Runner` to
instrument it.

Token usage is read from the run's shared `Usage`, one entry per model
request, and sent on every `stop` event and in the session summary. Calls
made by agents run as tools share that `Usage` and are counted too.

## API

| Function | Returns |
|---|---|
| `instrument(runner, options?)` | Listens on a `Runner`'s events; returns the runner |
| `flush()` | Waits until every event received so far has been handled |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |
| `adapter()` | Escape hatch — returns the underlying `DefaultAdapter` |

## Environment variables

Same as every `@agentegrity/*` adapter:

- `AGENTEGRITY_URL` (default `http://localhost:8787`)
- `AGENTEGRITY_TOKEN`
- `AGENTEGRITY_DISABLED=1` — no-op every hook

## License

Apache-2.0
