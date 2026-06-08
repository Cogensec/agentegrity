# @agentegrity/claude-sdk

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for the Claude Agent SDK (`@anthropic-ai/claude-agent-sdk`). Mirrors the Python `agentegrity.claude` module 1:1.

## Install

```bash
npm i @agentegrity/claude-sdk @anthropic-ai/claude-agent-sdk
# or: bun add @agentegrity/claude-sdk @anthropic-ai/claude-agent-sdk
```

## Use

```ts
import { ClaudeSDKClient } from "@anthropic-ai/claude-agent-sdk";
import { hooks, report } from "@agentegrity/claude-sdk";

const client = new ClaudeSDKClient({ hooks: hooks() });
// ... run your agent ...
console.log(await report());
```

That's the full integration — three lines.

## Config

Override the default profile:

```ts
const h = hooks({ profile: { name: "my-agent", risk_tier: "high" } });
```

Environment variables:

| Var | Default | What |
|---|---|---|
| `AGENTEGRITY_URL` | `http://localhost:8787` | Exporter backend URL |
| `AGENTEGRITY_TOKEN` | _(none)_ | Bearer token |
| `AGENTEGRITY_DISABLED` | _(unset)_ | Set to `1` to no-op every hook |

## API

| Function | Returns |
|---|---|
| `hooks(options?)` | Hook object for `ClaudeSDKClient({ hooks })` |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |
| `adapter()` | Escape hatch — returns the underlying `DefaultAdapter` |

## License

Apache-2.0
