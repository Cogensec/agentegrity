# Agentegrity Quickstart

Three copy-paste blocks. Pick the one that matches your setup and run it.

## 1. Instrument an existing Claude Agent SDK agent

```bash
pip install "agentegrity[claude]"
```

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from agentegrity.claude import hooks, report

async with ClaudeSDKClient(options=ClaudeAgentOptions(hooks=hooks())) as sdk:
    await sdk.query("Summarize the latest LLM safety papers")
print(report())
```

`hooks()` lazily builds a default adapter with a generic `AgentProfile`, the full four-layer evaluator (adversarial, cortical, governance, recovery), and measure-only semantics. It never blocks tool calls unless you pass `enforce=True`. `report()` returns a session summary — evaluation count, attestation chain length, whether the chain verifies.

## 1b. Instrument LangChain / LangGraph, OpenAI Agents, CrewAI, or Google ADK

Same shape, one import per framework. Pick the extra that matches your stack:

```bash
pip install "agentegrity[langchain]"     # covers LangChain + LangGraph
pip install "agentegrity[openai-agents]"
pip install "agentegrity[crewai]"
pip install "agentegrity[google-adk]"
```

```python
# LangChain
from agentegrity.langchain import instrument_chain, report
chain = instrument_chain(my_chain); chain.invoke({"input": "..."}); print(report())

# LangGraph
from agentegrity.langchain import instrument_graph, report
graph = instrument_graph(my_compiled_graph); graph.invoke(state); print(report())

# OpenAI Agents SDK
from agents import Runner
from agentegrity.openai_agents import run_hooks, report
await Runner.run(agent, input="...", hooks=run_hooks()); print(report())

# CrewAI
from agentegrity.crewai import instrument, report
instrument(); crew.kickoff(); print(report())

# Google ADK
from agentegrity.google_adk import instrument, report
instrument(agent); runner.run(...); print(report())
```

Each module exposes the same `report()` / `reset()` / `adapter()` surface as `agentegrity.claude`, with `AgentProfile.default()` and the full four-layer evaluator. Pass `profile=`, `client=`, `enforce=True`, or `api_key=` to any of the entry points to override.

## 1c. Export session data to a dashboard or external sink

Every adapter exposes `register_exporter(exporter)` — subscribe anything that implements the `SessionExporter` protocol (`on_session_start`, `on_event`, `on_session_end`) and it receives live session data as JSON-ready dicts:

```python
from agentegrity.langchain import register_exporter, instrument_graph

class PrintExporter:
    async def on_session_start(self, session_id, adapter_name, profile):
        print(f"[{adapter_name}] session {session_id} started")
    async def on_event(self, session_id, event):
        print(f"  {event['event_type']}")
    async def on_session_end(self, session_id, summary):
        print(f"[{session_id}] score={summary}")

register_exporter(PrintExporter())
graph = instrument_graph(my_graph)
```

Exporter exceptions are caught and logged — the exporter can never break the instrumented agent. For a production dashboard, deploy the commercial [`agentegrity-pro`](https://github.com/cogensec/agentegrity-pro) backend (`docker compose up`) and point the agent at it by setting `AGENTEGRITY_URL` and `AGENTEGRITY_TOKEN` — the default adapter picks them up automatically and streams every session over the published exporter HTTP API.

**Non-Python agents** use the same contract via one of the six TypeScript adapters, each shipping a 2–3 line zero-config enable. Pick the one that matches your framework:

```bash
npm i @agentegrity/claude-sdk      # Claude Agent SDK — options.hooks = hooks()
npm i @agentegrity/langchain       # LangChain JS — callbacks: [instrument()]
npm i @agentegrity/openai-agents   # OpenAI Agents SDK — run(..., { hooks: runHooks() })
npm i @agentegrity/crewai          # CrewAI JS — instrument().attach(crew.events)
npm i @agentegrity/google-adk      # Google ADK — instrument(agent)
npm i @agentegrity/vercel-ai       # Vercel AI SDK — experimental_telemetry: instrument()
```

Each re-exports `registerExporter()`, `report()`, and `reset()` for the same fan-out contract as the Python adapters. The low-level `@agentegrity/client` reporter is still available for custom frameworks. The wire format is published as JSON Schema in `schemas/exporter/` and OpenAPI 3.1 in `schemas/openapi.yaml`, so any language can produce or consume events.

## 2. Score an arbitrary agent profile

```bash
pip install agentegrity
```

```python
from agentegrity import AgentegrityClient

client = AgentegrityClient()
score = client.evaluate(client.create_profile(name="my-agent"))
print(f"{score.composite:.3f}  ({score.action})")
```

Use this for one-off profile scoring, CI gates, or agents outside the Claude SDK.

## 3. Confidence test from the terminal

```bash
python -m agentegrity          # version + adapter availability
python -m agentegrity doctor   # end-to-end self-check, prints composite score
```

If `doctor` prints a composite score and `OK`, your install is wired correctly.

---

## Next steps

- **Custom thresholds, layer weights, and threat detectors** — drop down to `IntegrityEvaluator` and the individual layers (see the "Configuring the evaluator" section in the [README](../README.md)).
- **Cryptographic attestation signing** — `pip install "agentegrity[crypto]"` and call `AttestationRecord.sign(private_key)` to produce verifiable records.
- **LLM-backed cortical checks** — `pip install "agentegrity[llm]"` and pass `LLMCorticalCheck` instances to `CorticalLayer(llm_checks=[...])` for semantic reasoning-chain validation.
- **Governance policies** — customize `GovernanceLayer(policy_set=...)` or register custom policy rules. See [`spec/layers/governance-layer.md`](../spec/layers/governance-layer.md).
- **Full specification** — [`spec/SPECIFICATION.md`](../spec/SPECIFICATION.md) is the source of truth for the property definitions, layer contracts, and scoring methodology.
