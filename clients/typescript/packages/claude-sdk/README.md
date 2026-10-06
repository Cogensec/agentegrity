# @agentegrity/claude-sdk

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for the Claude Agent SDK (`@anthropic-ai/claude-agent-sdk`). Mirrors the Python `agentegrity.claude` module 1:1.

## Install

```bash
npm i @agentegrity/claude-sdk @anthropic-ai/claude-agent-sdk
# or: bun add @agentegrity/claude-sdk @anthropic-ai/claude-agent-sdk
```

## Use

```ts
import { query } from "@anthropic-ai/claude-agent-sdk";
import { hooks, observe, report } from "@agentegrity/claude-sdk";

for await (const message of query({ prompt: "...", options: { hooks: hooks() } })) {
  observe(message); // optional: exact token totals
}
console.log(await report());
```

`hooks()` returns the SDK's `Options["hooks"]` shape: one matcher per event
(`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
`SubagentStart`, `SubagentStop`, `PreCompact`, `Stop`), each letting the SDK
continue.

Token usage is read from the session transcript the hooks name, and from
each subagent's transcript, at every `Stop`. Only usage counts, request ids
and model names are kept. Passing each message to `observe()` replaces that
estimate with the SDK's own `modelUsage` totals, which include subagents.

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
| `hooks(options?)` | `Options["hooks"]` for `query()` |
| `observe(message)` | Records exact usage from `result` messages (optional) |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |
| `adapter()` | Escape hatch — returns the underlying `DefaultAdapter` |

## License

Apache-2.0
