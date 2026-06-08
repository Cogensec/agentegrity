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
import { runHooks, report } from "@agentegrity/openai-agents";

await Runner.run(agent, "hello", { hooks: runHooks() });
console.log(await report());
```

## API

| Function | Returns |
|---|---|
| `runHooks(options?)` | Hook object for `Runner.run(..., { hooks })` |
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
