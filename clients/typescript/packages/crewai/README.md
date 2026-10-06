# @agentegrity/crewai

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for **CrewAI TypeScript** ([`@crewai-ts/core`](https://www.npmjs.com/package/@crewai-ts/core)). Mirrors the Python `agentegrity.crewai` module.

## Install

```bash
npm i @agentegrity/crewai @crewai-ts/core
```

`@crewai-ts/core` requires Node 22 or later.

## Use

```ts
import { Agent, Crew, Task, crewaiEventBus } from "@crewai-ts/core";
import { instrument, report } from "@agentegrity/crewai";

const crew = new Crew({ agents: [agent], tasks: [task] });
const close = instrument(crewaiEventBus, { crew });
await crew.kickoff();
await close();
console.log(await report());
```

`instrument()` subscribes to CrewAI's event bus. Pass `crew` to declare its agents as the session's topology. Instrumenting the same bus again returns the existing subscription. The returned function unsubscribes and ends the session.

## Event mapping

| CrewAI event | agentegrity event |
|---|---|
| `crew_kickoff_started` | `user_prompt_submit` |
| `crew_kickoff_completed` | `stop` |
| `crew_kickoff_failed` | `stop` (with the error) |
| `task_started` | `task_started` |
| `agent_execution_started` | `subagent_start` |
| `agent_execution_completed`, `agent_execution_error` | `subagent_stop` |
| `tool_usage_started` | `pre_tool_use` |
| `tool_usage_finished` | `post_tool_use` |
| `tool_usage_error` | `post_tool_use_failure` |
| `llm_call_completed` | token usage |

## Token usage

Each model call is counted once by its CrewAI call id and reported on the `stop` event and in `report().usage`. CrewAI reports a call's provider usage, then the client's running total; when a call carries no usage of its own (native tool calling), the adapter counts the running total's growth since the client's previous call.

## API

| Function | Returns |
|---|---|
| `instrument(bus, options?)` | A function that unsubscribes and ends the session |
| `flush()` | Resolves once every received event has been handled |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |

## License

Apache-2.0
