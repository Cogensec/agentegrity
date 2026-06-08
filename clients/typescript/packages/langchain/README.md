# @agentegrity/langchain

Zero-config [agentegrity](https://github.com/cogensec/agentegrity) adapter for **LangChain JS** and **LangGraph JS**. Mirrors the Python `agentegrity.langchain` module 1:1.

## Install

```bash
npm i @agentegrity/langchain @langchain/core
# or: bun add @agentegrity/langchain @langchain/core
```

## Use

```ts
import { ChatAnthropic } from "@langchain/anthropic";
import { instrument, report } from "@agentegrity/langchain";

const llm = new ChatAnthropic({ callbacks: [instrument()] });
// ... run chain / graph — callbacks propagate to every child runnable ...
console.log(await report());
```

Works with **every LangChain JS runnable** — chains, tools, graphs, LLMs — because LangChain's callback system automatically propagates handlers down.

## Config

Override the default profile:

```ts
const cb = instrument({ profile: { name: "my-agent", risk_tier: "high" } });
```

Environment variables:

| Var | Default | What |
|---|---|---|
| `AGENTEGRITY_URL` | `http://localhost:8787` | Exporter backend URL |
| `AGENTEGRITY_TOKEN` | _(none)_ | Bearer token |
| `AGENTEGRITY_DISABLED` | _(unset)_ | Set to `1` to no-op the handler |

## API

| Function | Returns |
|---|---|
| `instrument(options?)` | A LangChain-compatible callback handler |
| `report()` | Session summary snapshot |
| `reset()` | Discard the module-global adapter |
| `registerExporter(exporter)` | Subscribe an additional `SessionExporter` |
| `adapter()` | Escape hatch — returns the underlying `DefaultAdapter` |

## License

Apache-2.0
