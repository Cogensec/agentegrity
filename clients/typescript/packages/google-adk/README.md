# @agentegrity/google-adk

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for the **Google Agent Development Kit for TypeScript** (`@google/adk`).

## Install

```bash
npm i @agentegrity/google-adk @google/adk
```

## Use

```ts
import { InMemoryRunner, LlmAgent } from "@google/adk";
import { instrument, report } from "@agentegrity/google-adk";

const agent = new LlmAgent({ name: "my-agent", model: "gemini-2.5-flash" });
instrument(agent);
const runner = new InMemoryRunner({ agent, appName: "app" });
const session = await runner.sessionService.createSession({ appName: "app", userId: "u" });
for await (const event of runner.runAsync({
  userId: "u",
  sessionId: session.id,
  newMessage: { role: "user", parts: [{ text: "hello" }] },
})) {
  // ...
}
console.log(await report());
```

`instrument()` adds callbacks to the agent and to every agent reachable from it (`subAgents`, and agents wrapped in an `AgentTool`), ahead of any callbacks you already set. ADK stops at the first callback that returns a value; these return nothing, so your own callbacks still run and decide.

| ADK callback | agentegrity event |
|---|---|
| `beforeAgentCallback` | `user_prompt_submit` (the agent you instrumented), `subagent_start` (any other) |
| `afterAgentCallback` | `stop` / `subagent_stop` |
| `beforeToolCallback` | `pre_tool_use` (tool name and arguments) |
| `afterToolCallback` | `post_tool_use` |
| `afterModelCallback` | token usage |

Token usage is counted per model call from `usageMetadata`, skipping streamed partial responses, and sent on every `stop` event and in the session summary. The prompt count already includes cached tokens; thinking tokens are added to output and tool-use prompt tokens to input. Live (bidirectional) mode does not run model callbacks, so its usage is not seen.

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
