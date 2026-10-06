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

Works with `generateText`, `streamText`, `generateObject`, `streamObject`, and the SDK's tool-calling path, on AI SDK 3.4 through 6.

### AI SDK 7

AI SDK 7 removed the per-call `tracer` option. Register the integration once at startup:

```ts
import { registerTelemetry } from "ai";
import { telemetry } from "@agentegrity/vercel-ai";

registerTelemetry(telemetry());
```

Register it globally rather than passing it in `telemetry.integrations` on a call: per-call integrations replace the global ones, which would drop any other telemetry you registered.

## How it works

The Vercel AI SDK emits OpenTelemetry spans with well-known names (`ai.generateText`, `ai.streamText`, `ai.toolCall`, etc.). `instrument()` returns a minimal tracer that maps those spans to agentegrity events:

| AI SDK span | agentegrity start event | agentegrity end event |
|---|---|---|
| `ai.generateText` / `ai.streamText` | `user_prompt_submit` | `stop` |
| `ai.generateObject` / `ai.streamObject` | `user_prompt_submit` | `stop` |
| `ai.toolCall` | `pre_tool_use` (name, arguments) | `post_tool_use` (result) or `post_tool_use_failure` |
| `*.doGenerate` / `*.doStream` | | token usage |

On AI SDK 7 the same events come from `onStart`, `onToolExecutionStart`, `onToolExecutionEnd`, `onLanguageModelCallEnd` and `onEnd`.

## Token usage

Usage is counted per model call (`doGenerate` / `doStream` spans, or `onLanguageModelCallEnd`) and sent on every `stop` event and in the session summary. The operation spans carry the sum of their calls and are not counted again. Counts are normalized so `input_tokens` includes cached tokens on every SDK version; Anthropic input, which excluded the cache before AI SDK 6, is corrected. AI SDK 5's non-streaming spans omit Anthropic cache reads, so those calls are marked incomplete.

The tracer ships no `@opentelemetry/api` runtime dependency — it implements just enough of the Span/Tracer surface the AI SDK uses.

Pass `{ catchAll: true }` to also emit events for unrecognized span names.

## API

| Function | Returns |
|---|---|
| `instrument(options?)` | `{ isEnabled: true, tracer }` for `experimental_telemetry` (AI SDK 3.4 to 6) |
| `telemetry(options?)` | Integration for `registerTelemetry()` (AI SDK 7) |
| `flush()` | Waits until every event received so far has been handled |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |

## License

Apache-2.0
