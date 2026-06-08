# @agentegrity/vercel-ai

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for the **Vercel AI SDK** (`ai`).

TypeScript-native — there is no Python equivalent.

## Install

```bash
npm i @agentegrity/vercel-ai ai
```

## Use

```ts
import { streamText } from "ai";
import { instrument, report } from "@agentegrity/vercel-ai";

const { textStream } = streamText({
  model: anthropic("claude-sonnet-4-5"),
  prompt: "Summarize the latest papers",
  experimental_telemetry: instrument(),
});

for await (const chunk of textStream) process.stdout.write(chunk);
console.log(await report());
```

Works with `generateText`, `streamText`, `generateObject`, `streamObject`, and the SDK's tool-calling path.

## How it works

The Vercel AI SDK emits OpenTelemetry spans with well-known names (`ai.generateText`, `ai.streamText`, `ai.toolCall`, etc.). `instrument()` returns a minimal tracer that maps those spans to agentegrity events:

| AI SDK span | agentegrity start event | agentegrity end event |
|---|---|---|
| `ai.generateText` / `ai.streamText` | `user_prompt_submit` | `stop` |
| `ai.generateObject` / `ai.streamObject` | `user_prompt_submit` | `stop` |
| `ai.toolCall` | `pre_tool_use` | `post_tool_use` |

The tracer ships no `@opentelemetry/api` runtime dependency — it implements just enough of the Span/Tracer surface the AI SDK uses.

Pass `{ catchAll: true }` to also emit events for unrecognized span names.

## API

| Function | Returns |
|---|---|
| `instrument(options?)` | `{ isEnabled: true, tracer }` for `experimental_telemetry` |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |

## License

Apache-2.0
