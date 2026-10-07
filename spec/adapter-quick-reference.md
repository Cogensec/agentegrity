# Adapter Quick Reference

Every first-party agentegrity adapter follows the same shape: zero-config by default, import one function, wire it into the framework's callback / hook / telemetry slot, and events stream through any `SessionExporter` you register.

There are **fourteen framework adapters** across two languages, plus a hook runtime for two coding-agent hosts.

## Python (eight)

Installed with `pip install agentegrity` plus the framework's extra. Every module re-exports `register_exporter`, `report`, and `reset`, and every entry point accepts `enforce=True`.

| Framework | Module | Enable snippet | Enforcement |
|---|---|---|---|
| Claude Agent SDK | `agentegrity.claude` | `ClaudeSDKClient(options=ClaudeAgentOptions(hooks=hooks()))` | deny via hook return |
| LangChain / LangGraph | `agentegrity.langchain` | `graph = instrument_graph(my_graph)` | callback return |
| OpenAI Agents SDK | `agentegrity.openai_agents` | `Runner.run(agent, input=..., hooks=run_hooks())` | RunHooks return |
| CrewAI | `agentegrity.crewai` | `instrument(crew)` | event-bus deny |
| Google ADK | `agentegrity.google_adk` | `instrument(agent)` | observation only |
| AutoGen | `agentegrity.autogen` | `instrument()` | observation only |
| Agno | `agentegrity.agno` | `agent = instrument(agent)` / `team = instrument_team(team)` | `StopAgentRun` |
| AWS Bedrock Agents | `agentegrity.bedrock_agents` | `agent = instrument_strands(agent)` / `client = wrap_client(client)` | Strands: `cancel_tool`; boto3: observation only |

## TypeScript (six)

One npm package per framework; all depend on `@agentegrity/client` for the shared `createDefaultAdapter()` helper. Every package re-exports `registerExporter`, `report`, `reset`, and `adapter`.

| Framework | npm package | Enable snippet |
|---|---|---|
| Claude Agent SDK | `@agentegrity/claude-sdk` | `query({ options: { hooks: hooks() } })` |
| LangChain JS | `@agentegrity/langchain` | `new ChatX({ callbacks: [instrument()] })` |
| OpenAI Agents SDK | `@agentegrity/openai-agents` | `const runner = instrument(new Runner())` |
| CrewAI TypeScript | `@agentegrity/crewai` | `const close = instrument(crewaiEventBus, { crew })` |
| Google ADK | `@agentegrity/google-adk` | `const close = instrument(agent)` |
| Vercel AI SDK *(TS-only)* | `@agentegrity/vercel-ai` | `streamText({ experimental_telemetry: instrument() })`; AI SDK 7: `registerTelemetry(telemetry())` |

## Coding-agent hosts (hook runtime)

Coding agents are instrumented through their own hook systems, not a library call. Each host runs `python3 -m agentegrity hook --host <host>` on every hook event; the runtime (`agentegrity.hooks`) keeps a daemon per session so the layers see the whole conversation. Install the plugin from this repo's marketplace.

| Host | Plugin | Adapter | Escalate verdict | Failure signal |
|---|---|---|---|---|
| Claude Code | `integrations/claude-code` | `ClaudeCodeAdapter` (`claude_code`) | `ask` (approval prompt) | `PostToolUseFailure` |
| Codex | `integrations/codex` | `CodexAdapter` (`codex`) | `deny` (Codex enforces only deny) | non-zero `tool_response.exit_code` |

Block verdicts deny on both hosts. Configuration is shared: `AGENTEGRITY_HOOK_MODE` (`enforce` default, or `alert`), `AGENTEGRITY_HOOK_DISABLED`, `AGENTEGRITY_RISK_TIER`, `AGENTEGRITY_HOOK_DIR`, `AGENTEGRITY_HOOK_IDLE_SECONDS`, `AGENTEGRITY_AGENT_ID` and `AGENTEGRITY_AGENT_NAME` (console grouping and display name, both defaulting to the host), `AGENTEGRITY_MODEL_ID` (model reported on the profile, unset by default). Decision chains are written to `<hook dir>/<host>/<session>.chain.json`. Without Unix sockets (Windows) each call is evaluated in-process from the persisted chain: single-call rules enforce, cross-call rules and streaming do not.

## Shared guarantees

All fourteen framework adapters conform to the same contract:

- **Zero-config**: reads `AGENTEGRITY_URL` and `AGENTEGRITY_TOKEN` from the environment. No explicit client wiring required.
- **Kill switch**: `AGENTEGRITY_DISABLED=1` (or `AGENTEGRITY_DISABLE=1`) bypasses the adapter entirely.
- **Fail-open**: exporter exceptions are caught and logged; the instrumented agent never breaks because of the adapter.
- **Event vocabulary**: every adapter maps framework-specific hooks onto the canonical event types (`user_prompt_submit`, `pre_tool_use`, `post_tool_use`, `post_tool_use_failure`, `subagent_start`, `subagent_stop`, `pre_compact`, `stop`, plus the v0.8 multi-agent events). `schemas/exporter/common.json` lists every type the SDK sends in `event_type.examples` and accepts any snake_case type, so a consumer validating against an older copy does not reject a newer event; handle the types you know and accept the rest.
- **Argument classification**: every `pre_tool_use` is tagged by `classify_tool_call()` (`reads_sensitive`, `sends_external`, `remote_code_exec`, `log_tamper`, `obfuscated_command`), so generic shell tools are judged by what they run, not by their name.
- **Idempotent**: instrumenting the same agent / graph / runner / event bus twice is a no-op.
- **Version parity**: Python `pyproject.toml` and every `@agentegrity/*` package publish with the same version string (enforced in CI by `clients/typescript/scripts/check-versions.ts`).

## Token usage

Adapters put the session's running token usage on each `stop` event and in the session summary, normalized so `input_tokens` always includes cached tokens. Reported by both coding-agent hosts, all eight Python adapters and all six TypeScript packages. Field meanings, provenance and per-integration sources: [token-usage.md](token-usage.md).

## Wire format

All adapters emit the same JSON payloads. The contract is authoritative:

- JSON Schema: `schemas/exporter/`
- OpenAPI 3.1: `schemas/openapi.yaml`

Python drift is caught by `tests/test_schemas.py`; TypeScript packages share the same shape via the `EmittableEvent` type in `@agentegrity/client`.
