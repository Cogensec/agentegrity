<p align="center">
  <img src="assets/logo-dark.png" alt="Agentegrity" />
</p>

<p align="center">
<a href="https://npmjs.com/package/@agentegrity/client"><img src="https://img.shields.io/npm/v/@agentegrity/client" alt="npm"></a>
<a href="https://npmjs.com/package/@agentegrity/client"><img src="https://img.shields.io/npm/dm/@agentegrity/client" alt="npm"></a>
<a href="https://www.npmjs.com/org/agentegrity"><img src="https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FCogensec%2Fagentegrity%2Fmain%2Fbadges%2Fnpm-downloads.json" alt="npm downloads"></a>
<a href="https://pepy.tech/projects/agentegrity"><img src="https://static.pepy.tech/personalized-badge/agentegrity?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=pypi-downloads" alt="PyPI Downloads"></a>
<a href="https://deepwiki.com/Cogensec/agentegrity"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki"></a>
<a href="https://opensource.org/licenses/Apache-2.0">
  <img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+"></a>
<a href="pyproject.toml"><img src="https://img.shields.io/badge/library-v0.12.0-green.svg" alt="Library Version"></a>
<a href="spec/SPECIFICATION.md"><img src="https://img.shields.io/badge/spec-v1.0--draft-blue.svg" alt="Spec Version"></a
<a href="https://github.com/cogensec/agentegrity"><img src="https://komarev.com/ghpvc/?username=cogensec&color=1E3A5F&style=flat-square&label=Profile+Views" alt="Profile Views" />
</a>

</p>

## Supported Frameworks

Fourteen zero-config framework adapters with the same three-line instrumentation, plus hook plugins for two coding agents. The Python adapters and the hook plugins share one attestation chain format, signed when you configure a key; the TypeScript packages capture and stream events without evaluating or attesting locally.

<table>
  <tr>
    <td align="center" width="110">
      <a href="https://github.com/anthropics/claude-agent-sdk-python">
        <img src="https://github.com/anthropics.png" width="48" alt="Claude Agent SDK"/><br/>
        <sub><b>Claude Agent SDK</b></sub>
      </a><br/><sub>Python · TS</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/langchain-ai/langchain">
        <img src="https://github.com/langchain-ai.png" width="48" alt="LangChain / LangGraph"/><br/>
        <sub><b>LangChain / LangGraph</b></sub>
      </a><br/><sub>Python · TS</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/openai/openai-agents-python">
        <img src="https://github.com/openai.png" width="48" alt="OpenAI Agents SDK"/><br/>
        <sub><b>OpenAI Agents SDK</b></sub>
      </a><br/><sub>Python · TS</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/crewAIInc/crewAI">
        <img src="https://github.com/crewAIInc.png" width="48" alt="CrewAI"/><br/>
        <sub><b>CrewAI</b></sub>
      </a><br/><sub>Python · TS</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/google/adk-python">
        <img src="https://github.com/google.png" width="48" alt="Google ADK"/><br/>
        <sub><b>Google ADK</b></sub>
      </a><br/><sub>Python · TS</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="110">
      <a href="https://github.com/microsoft/autogen">
        <img src="https://github.com/microsoft.png" width="48" alt="AutoGen"/><br/>
        <sub><b>AutoGen</b></sub>
      </a><br/><sub>Python</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/agno-agi/agno">
        <img src="https://github.com/agno-agi.png" width="48" alt="Agno"/><br/>
        <sub><b>Agno</b></sub>
      </a><br/><sub>Python</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/awslabs">
        <img src="https://github.com/aws.png" width="48" alt="AWS Bedrock Agents"/><br/>
        <sub><b>AWS Bedrock Agents</b></sub>
      </a><br/><sub>Python</sub>
    </td>
    <td align="center" width="110">
      <a href="https://github.com/vercel/ai">
        <img src="https://github.com/vercel.png" width="48" alt="Vercel AI SDK"/><br/>
        <sub><b>Vercel AI SDK</b></sub>
      </a><br/><sub>TypeScript</sub>
    </td>
    <td align="center" width="110"></td>
  </tr>
  <tr>
    <td align="center" width="110">
      <a href="integrations/claude-code/README.md">
        <img src="https://github.com/anthropics.png" width="48" alt="Claude Code"/><br/>
        <sub><b>Claude Code</b></sub>
      </a><br/><sub>hook plugin</sub>
    </td>
    <td align="center" width="110">
      <a href="integrations/codex/README.md">
        <img src="https://github.com/openai.png" width="48" alt="Codex"/><br/>
        <sub><b>Codex</b></sub>
      </a><br/><sub>hook plugin</sub>
    </td>
    <td align="center" width="110"></td>
    <td align="center" width="110"></td>
    <td align="center" width="110"></td>
  </tr>
</table>

<sub>All product names, logos, and brands are property of their respective owners and are used for identification purposes only. Use does not imply endorsement.</sub>


**Building AI agents capable of securing themselves.**

Every existing AI security tool builds protection that humans apply to agents from the outside. Guardrails filter inputs. Runtime monitors watch outputs. Policy engines enforce rules. These are necessary, and Agentegrity does not replace them. Agentegrity addresses a different question: how do you measure whether the agent itself has the structural integrity to remain coherent when those external controls cannot reach inside its decision process?

Agentegrity (agent + integrity) is the discipline of building AI agents that can defend themselves, stabilize themselves, and recover themselves — and then verifying that they actually can. This repository provides the open specification, the reference architecture, and a Python implementation for that verification.

---

## Why This Matters Now

Frontier model labs ship better base models on a regular cadence. Each new release reduces the rate at which the underlying model produces unsafe outputs in isolated benchmarks. This is real progress, and it does not solve the agent security problem.

Enterprises do not deploy base models. They deploy compositions: a base model wrapped in system prompts, augmented with retrieval over private data, given access to tools that touch customer systems, equipped with persistent memory, orchestrated through planning loops, and embedded in environments that produce inputs the model was never trained against. Every capability gain in the underlying model enables more ambitious compositions with more attack surface. The composition layer is where security failures occur, and the composition layer is not what the model labs are improving.

Agentegrity is positioned at the composition layer specifically. Its measurements are about whether the assembled agent — not the underlying model — has the structural properties required to maintain integrity under adversarial pressure, across deployment contexts, and over time.

---

## The Three Self-Securing Capabilities

A self-securing agent maintains three properties simultaneously. Each property is a capability the agent has, not a control imposed on it from outside. The Agentegrity Framework defines how to verify each one.

| Capability | What The Agent Does | What This Prevents |
|---|---|---|
| **Self-Defense** | Maintains coherent reasoning under adversarial pressure across all input channels | Goal hijacking, prompt injection, indirect injection via retrieved content, tool output poisoning |
| **Self-Stability** | Monitors its own behavioral drift against an established baseline and detects internal state corruption | Slow-drift attacks, memory poisoning, gradual goal redirection, identity erosion |
| **Self-Recovery** | Detects when its integrity has been compromised and restores itself to a known-good state | Persistent compromise, undetected lateral movement, state pollution across sessions |

v0.12.0 ships verification for all three capabilities (self-defense via the adversarial layer, self-stability via the cortical layer with optional LLM-backed semantic checks, self-recovery via the recovery layer with persistable checkpoint round-trip), **decision provenance** (hash-chained `DecisionRecord`s, signed when a key is configured, captured at every decision boundary), and **multi-agent topology** (`AgentTopology` declared at instrument time for every framework with multi-agent primitives, surfaced as `Evidence` so the chain commits to the structural shape the agent participated in) across **fourteen zero-config framework adapters**: eight in Python (**Claude Agent SDK**, **LangChain / LangGraph**, **OpenAI Agents SDK**, **CrewAI**, **Google ADK**, **AutoGen**, **Agno**, **AWS Bedrock Agents**) and six in TypeScript (the original five plus **Vercel AI SDK** which has no Python equivalent). All fourteen share the `SessionExporter` extension point that lets any subscriber (including the commercial `agentegrity-pro` dashboard) receive live session data without touching the agent. The eight Python adapters also share one evaluator pipeline and attestation chain, signed with Ed25519 when the adapter has a `signing_key`. The six TypeScript packages capture and stream events but run no local evaluation and keep no chain, and their summary says so (`chain_hash_linked: null`, `chain_signatures: "none"`).

---

## The Four Layers

The framework implements verification through four architectural layers. Each layer addresses a different dimension of integrity. Together they form a complete envelope around the agent.

```
┌─────────────────────────────────────────────┐
│            RECOVERY LAYER                   │
│   Compromise detection · Continuity ·       │
│   Sustained-degradation tracking            │
├─────────────────────────────────────────────┤
│           GOVERNANCE LAYER                  │
│   Policy enforcement · Human oversight ·    │
│   Compliance mapping · Audit trails         │
├─────────────────────────────────────────────┤
│            CORTICAL LAYER                   │
│   Reasoning consistency · Memory checks ·   │
│   Behavioral baselines · Drift detection    │
├─────────────────────────────────────────────┤
│           ADVERSARIAL LAYER                 │
│   Attack surface mapping · Threat           │
│   detection · Coherence scoring             │
└─────────────────────────────────────────────┘
```

The **Adversarial Layer** verifies self-defense by mapping the agent's attack surface and detecting threats across input channels. The **Cortical Layer** verifies self-stability by monitoring reasoning consistency, memory integrity, and behavioral drift from baseline. The **Governance Layer** enforces organizational policy and produces audit trails so verification results have a place to live in compliance workflows. The **Recovery Layer** verifies self-recovery by tracking the attestation chain for continuity, watching score history for sustained degradation, and counting the recovery mechanisms actually attached (today, a `Checkpoint` backend). Capabilities a profile only declares are reported as unbacked and do not raise the score.

---

## What This Library Does (and Does Not)

We believe in being explicit about what the library is and is not, because a security library that overpromises is worse than one that underdelivers.

**What it does.** It provides a Python implementation of the four-layer verification architecture defined in the [Agentegrity Specification](spec/SPECIFICATION.md). It computes integrity scores from real evaluation runs, generates attestation records (signed with Ed25519 when you configure a key), builds tamper-evident attestation chains, and produces structured audit logs for governance workflows. Evaluation runs locally with zero required dependencies. Apart from telemetry, nothing leaves your process unless you turn it on, and each path is switched on separately. **Anonymous, shape-only telemetry** (enum values, counts, rounded scores; never prompts, tool arguments, or agent content) is on by default and disabled by `DO_NOT_TRACK=1`; every event is documented in [docs/telemetry.md](docs/telemetry.md). **Sinks you attach** run only when configured, in code or by setting `AGENTEGRITY_TOKEN` and `AGENTEGRITY_EXPORTER_URL`: session exporters stream full event content, including prompts and tool arguments, while webhook and Slack alerts carry verdict shape only. `report()["exporters"]` names every attached sink, so an empty list means the run stayed local. **Model-backed layers you opt into** send the content they classify to the model you point them at: the `[llm]` layers call Anthropic's API, and `AdversarialSLMLayer` calls `AGENTEGRITY_SLM_BASE_URL` (a local Ollama server by default). See [Telemetry](#telemetry-and-how-to-opt-out) below. It ships with extension points for custom threat detectors, custom policy rules, and custom validators.

**What it does not do.** The adversarial layer ships a regex pattern taxonomy across eight attack families (prompt_injection, jailbreak, role_confusion, system_prompt_extraction, data_exfiltration, prompt_obfuscation, action_injection, tool_poisoning). Calibration is published in STATUS.md, weak numbers included: 1.000 TPR / 0.000 FPR on InjecAgent (N=2,108) after the action_injection family landed — calibrated on that suite, so read it as in-distribution recall — and **0.286 TPR / 0.113 FPR on AgentDojo's goal-text projection**, which regex cannot close because separating an injected goal from a legitimate user task requires session context, not content patterns. Paraphrased or non-English action injections will evade the regex tier; the LLM/SLM classifier layers are the semantic backstop. The cortical layer uses Jensen-Shannon distance with Laplace smoothing for drift detection (replaces the older asymmetric KL approximation) and structural memory-provenance inspection. v0.2.0 introduced optional LLM-backed cortical checks (`pip install agentegrity[llm]`) that use Claude for semantic reasoning-chain validation, memory-provenance analysis, and drift classification; these run alongside the pattern-based checks and fail open on API errors. Production deployments should also register custom detectors with domain-specific logic. The library ships fourteen framework adapters: eight in Python (Claude Agent SDK, LangChain / LangGraph, OpenAI Agents SDK, CrewAI, Google ADK, AutoGen, Agno, AWS Bedrock Agents) and six in TypeScript (the original five plus Vercel AI SDK). Coding agents (Claude Code, Codex) are covered by hook plugins on a shared runtime. Tool calls are classified by their arguments (`classify_tool_call()`), which is static analysis of what a command says: anything the shell computes at runtime (variable-built paths, command substitution, `$IFS` splicing) is flagged as obfuscated, not resolved, and Codex hook gaps (`write_stdin`, still-running commands, hosted tools) cannot be closed from a hook. The Semantic Kernel adapter is deferred pending Microsoft Agent Framework GA (Q2 2026); one MAF adapter will cover both.


**What it deliberately is not.** It is not a guardrail. It blocks nothing unless you turn enforcement on (`enforce=True` on an adapter; the coding-agent plugins default to enforce and offer `alert` mode), and every verdict is recorded with its reason on the chain. With the default layers the cause is an explicit rule or a measured threshold: a governance policy, a tool-call category such as remote code execution or log tampering, a high-severity regex match, or drift and degradation past a configured bound. The opt-in LLM and SLM classifier layers can also block on a model's verdict (severity of 0.90 or more), so enable them under enforcement only after measuring them on your traffic. The Python OSS library also provides an **opt-in, host-installed authorization gate** (`ActionSecurityGate`) that can deny unauthorized tools at the real execution boundary when callers use `invoke()` or `ainvoke()`. Adapter hook verdicts are only enforceable where the host actually honors a denial. It is **not** a universal interception layer, tool sandbox, hosted service, or guarantee that a downstream effect occurred. Its central purpose remains measuring and verifying agent integrity with defensible evidence.

---

## Runtime Action Authority (opt-in, Python OSS)

In addition to integrity scoring and attestation, the **current repository source**
includes a trusted-host authorization API for consequential agent tool calls.
This work was merged after the v0.12.0 release documented above; **its presence
on `main` is not a claim that every published PyPI or npm package includes it**.
Check the installed package and its release notes before relying on these APIs.

`TaskMandate` defines who may act, for which run, tools, resources, effects
and credentials, and for how long. `ToolAuthority` is a separate trusted
registry of each tool's real effect and resource binding. An
`ActionSecurityGate` checks both at a tool's execution boundary. Neither
permissions nor trusted source labels should be derived from model text,
retrieved documents, peer-agent messages, or MCP/tool descriptions.

| Runtime failure pattern | Opt-in capability | Detailed guide |
|---|---|---|
| **Standing access** | Explicit, expiring task mandates with tool/resource/effect/credential allowlists | [Runtime Action Authority](docs/RUNTIME-ACTION-AUTHORITY.md) |
| **First-call effects** | `invoke()` / `ainvoke()` enforce before executing, and distinguish permission from actual side effects | [Runtime Action Authority](docs/RUNTIME-ACTION-AUTHORITY.md) |
| **Unclear trust boundaries** | Host-supplied `ActionProvenance` and protected arguments must originate from authorized sources | [Trust Boundaries and Data-as-Control](docs/TRUST-BOUNDARIES-AND-DATA-CONTROL.md) |
| **Data-as-control** | Untrusted content cannot supply authority for protected tool parameters when accurate host lineage is provided | [Trust Boundaries and Data-as-Control](docs/TRUST-BOUNDARIES-AND-DATA-CONTROL.md) |
| **Cascading delegation** | `delegate()` narrows child scope and lifetime; ancestor revocation applies in-process | [Cascading Delegation](docs/CASCADING-DELEGATION-AUTHORITY.md) |
| **Late-arriving evidence** | Explicit `PENDING` / `UNKNOWN` / `CONFIRMED` / `FAILED` / `CONFLICTED` receipt states; host verification and timeouts | [Late-Arriving Effect Evidence](docs/LATE-ARRIVING-EFFECT-EVIDENCE.md) |

**Security boundaries:** These features are **off unless configured**. A
pre-tool adapter hook alone is not a universal hard gate; for reliable denial,
put `invoke()` / `ainvoke()` around the actual trusted tool executor.
Delegation and evidence tracking are process-local, not distributed or
durable. Provenance must be captured outside model control. A successful
executor result does **not** independently verify a committed downstream
effect; an application-owned verifier must check the acting system and bind
evidence to the correct action, principal, run, resource and effect. Evidence
deadlines require the host to call `expire_unresolved_effects()`; they do not
run in a background worker. The TypeScript event-streaming packages do not
automatically inherit these Python enforcement semantics.

The [runnable synthetic banking example](examples/action_authority_evidence.py)
demonstrates a denied out-of-scope action, an unverified pending receipt, and
an in-memory simulated ledger reconciliation. It deliberately uses **no real
banking system or cryptographic downstream attestation**.

---

## Quick Start

### Installation

```bash
pip install "agentegrity[claude]"          # Claude Agent SDK
pip install "agentegrity[langchain]"       # LangChain + LangGraph
pip install "agentegrity[openai-agents]"   # OpenAI Agents SDK
pip install "agentegrity[crewai]"          # CrewAI
pip install "agentegrity[google-adk]"      # Google Agent Development Kit
```

Other extras: `[crypto]` (Ed25519 attestation signing), `[llm]` (LLM-backed cortical checks via the Anthropic API), `[all]` (everything).

### Task-scoped tool authorization and evidence (repository source)

Run the **offline, test-covered** example from a checkout with the Python
package installed (for example, `pip install -e .`):

```bash
python examples/action_authority_evidence.py
```

Expected output:

```text
Out-of-scope action: denied
Before simulated evidence verification: pending
After simulated evidence verification: confirmed
```

The example uses `ActionSecurityGate.invoke()` to stop a resource not in the
task mandate **before any executor call**. The authorized synthetic operation
starts as `PENDING`; a tool-returned claim alone is still not accepted. A
trusted-host callback then compares the receipt against a separately maintained
**in-memory simulated ledger**. This is an API demonstration, **not independent
verification of a real external side effect**. Production integrations must
authenticate the real downstream evidence source. See
[Late-Arriving Effect Evidence](docs/LATE-ARRIVING-EFFECT-EVIDENCE.md) for
unknown, delayed and conflicting outcomes, and
[Cascading Delegation](docs/CASCADING-DELEGATION-AUTHORITY.md) for subagents.

### Telemetry (and how to opt out)

Agentegrity collects **anonymous, shape-only usage analytics**: adapter names, enum values, counts, and rounded scores. It never collects prompts, model inputs or outputs, tool arguments, file paths, agent names, or any other content. No event is sent on import; a random, resettable UUID at `~/.agentegrity/id` is the only identifier.

Opt out any time, no code changes needed:

```bash
export DO_NOT_TRACK=1                      # the cross-tool standard, or:
export AGENTEGRITY_TELEMETRY_DISABLED=1    # agentegrity-specific
```

Or at runtime:

```python
import agentegrity
agentegrity.disable_telemetry()
```

When disabled, nothing is written, no thread starts, and no network is touched. Every event and property is documented in [docs/telemetry.md](docs/telemetry.md), and all payload construction is auditable in one file: [`_telemetry_props.py`](src/agentegrity/core/_telemetry_props.py).

### Two-line start (any supported framework)

`init()` probes the environment for every framework the adapter
registry knows, builds one adapter per detected framework on a shared
client, and auto-subscribes the ones that attach globally (CrewAI's
event bus). Object-level frameworks go through
`runtime.instrument(obj)`, which dispatches on the object's type:

```python
import agentegrity

runtime = agentegrity.init()              # detect + attach
chain = runtime.instrument(my_chain)      # LangChain / LangGraph / Agno / Strands / ADK
chain.invoke({"input": "..."})
print(runtime.report())
agentegrity.shutdown()                    # end sessions, flush exporters
```

Hook-style frameworks (Claude Agent SDK, OpenAI Agents SDK) hand you
their hooks object via `runtime.adapters["claude"].create_hooks()` /
`runtime.adapters["openai_agents"].create_run_hooks()`. The
per-framework modules below remain the fully explicit path.

### Instrument an existing Claude Agent SDK agent

Three lines of agentegrity, zero configuration. `hooks()` lazily builds a default adapter with a sensible `AgentProfile`, the full four-layer evaluator, and measure-only semantics (it never blocks tool calls).

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from agentegrity.claude import hooks, report

async with ClaudeSDKClient(options=ClaudeAgentOptions(hooks=hooks())) as sdk:
    await sdk.query("Summarize the latest LLM safety papers")
print(report())
```

`report()` returns the session summary: evaluation count, attestation chain length, whether the chain is hash-linked, whether and by whom it is signed (`chain_signatures`), and enforcement mode. Records are unsigned unless the adapter has a `signing_key` (requires the `[crypto]` extra), and only `get_summary(trusted_keys=...)` or `agentegrity verify-decisions --trusted-key` shows who signed them.

### Instrument LangChain / LangGraph, OpenAI Agents, CrewAI, or Google ADK

Same three-line shape for every supported framework:

```python
# LangChain or LangGraph (one adapter, both frameworks)
from agentegrity.langchain import instrument_chain, instrument_graph, report
chain = instrument_chain(my_chain); chain.invoke({"input": "..."}); print(report())

# OpenAI Agents SDK
from agents import Runner
from agentegrity.openai_agents import run_hooks, report
await Runner.run(agent, input="...", hooks=run_hooks()); print(report())

# CrewAI
from agentegrity.crewai import instrument, report
instrument(); crew.kickoff(); print(report())

# Google ADK
from agentegrity.google_adk import instrument, report
instrument(agent); print(report())
```

Every adapter uses the same default profile, evaluator, and attestation chain as the Claude path. Pass `profile=`, `client=`, or `enforce=True` to override.

Quick sanity check from the terminal:

```bash
python -m agentegrity          # version + installed adapters
python -m agentegrity doctor   # end-to-end self-check, prints composite score
```

### Instrument a multi-agent system (v0.8+)

For frameworks with multi-agent primitives (teams, crews, collaborators, graphs, handoffs), the adapter declares an `AgentTopology` at instrument time. The chain commits to the structure via `Evidence(evidence_type="topology")` so a downstream verifier can prove which topology the agent participated in. The four layers automatically gain peer-authority scanning, cascade detection, role-conformant drift, and `GOV-004` gating once the topology is declared.

```python
# Agno team — instrument_team walks team.members → HUB_SPOKE
from agentegrity.agno import instrument_team, report
team = instrument_team(team); team.run("..."); print(report())

# CrewAI crew — instrument(crew) walks crew.agents → HUB_SPOKE / HIERARCHICAL_DAG
# (TaskStartedEvent → task_started, not subagent_start.)
from agentegrity.crewai import instrument, report
instrument(crew); crew.kickoff(); print(report())

# AWS Bedrock Agents — collaborators discovered in the trace stream grow the topology
from agentegrity.bedrock_agents import wrap_client, instrument_strands
client = wrap_client(boto_client)  # boto3 path (observation-only)
agent  = instrument_strands(strands_agent)  # Strands path (real enforce)

# AutoGen — incremental GROUP_CHAT from OTel invoke_agent span hierarchy
from agentegrity.autogen import instrument
instrument()  # installs the SpanProcessor; root + nested spans grow topology

# Google ADK — walks SequentialAgent / ParallelAgent / LoopAgent.sub_agents
from agentegrity.google_adk import instrument
instrument(workflow_agent)  # HIERARCHICAL_DAG; plain Agent stays single-agent

# LangGraph — walks graph.get_graph().nodes (supervisor pattern → HIERARCHICAL_DAG,
# swarm → PEER_TO_PEER)
from agentegrity.langchain import instrument_graph
instrument_graph(compiled_graph)

# OpenAI Agents handoffs — PEER_TO_PEER seeded on first agent; topology grows on each handoff
from agentegrity.openai_agents import run_hooks
await Runner.run(agent, input="...", hooks=run_hooks())
```

Verify the resulting chain — both single-agent decision links and (when peer chains are available) cross-agent links:

```bash
python -m agentegrity verify-decisions chain.json
```

```python
# Programmatic: walk peer_message / handoff Evidence into peer agents' chains
ok = adapter.attestation_chain.verify_cross_agent_links({
    "peer-1": peer1.attestation_chain,
    "peer-2": peer2.attestation_chain,
})
```

Claude Agent SDK is single-agent at the framework level — no topology is declared, and the conformance suite pins the absence. Multi-agent layer checks (cascade detection, peer-authority, `GOV-004` gating, role-conformant drift via per-role `BaselineStore` keys) silently no-op for single-agent deployments.

### Instrument a coding agent (Claude Code, Codex)

Coding agents run commands through one generic shell tool, so the SDK
ships a hook runtime for them: `agentegrity hook --host claude-code|codex`
keeps a daemon per session, classifies each command by its arguments,
scans tool output, gates sensitive-data egress and writes a verifiable
decision chain. Install the plugin for your host from this repo's
marketplace ([Claude Code](integrations/claude-code/README.md),
[Codex](integrations/codex/README.md)).

```bash
pip install "agentegrity[crypto]>=0.12.0"
codex plugin marketplace add cogensec/agentegrity          # Codex
# Claude Code: /plugin marketplace add cogensec/agentegrity
```

### Stream to a dashboard in one command

Point an already-instrumented agent at an [**`agentegrity-pro`**](https://app.cogensec.com) dashboard with no code changes:

```bash
# verify the connection
agentegrity pro --ingest-token agk_live_… --url https://your-dashboard --push

# run your agent with streaming enabled
agentegrity pro --ingest-token agk_live_… --url https://your-dashboard -- python my_agent.py
```

The first form checks the token and prints which workspace it belongs to. The second sets `AGENTEGRITY_TOKEN` / `AGENTEGRITY_EXPORTER_URL` and execs your command; adapters constructed inside it self-attach the built-in `HTTPExporter`, so nothing in your agent changes. Setting those two variables yourself has the same effect.

Delivery runs on a background daemon thread: ordered (the ingest API rejects an event for a session it has not seen yet), stdlib-only, and fail-open — a dashboard outage never surfaces in the agent.

Attaching is never silent. Unlike telemetry, an exporter streams **full event content** (prompts, tool arguments, tool outputs) to the URL you configure, so the SDK logs the destination at INFO when it attaches and lists every attached sink under `exporters` in `report()`. An empty `exporters` list is the proof a run stayed local. Neither surface ever contains the token.

### Export session data to any sink

Every adapter exposes `register_exporter(exporter)`. Implement three async methods — `on_session_start`, `on_event`, `on_session_end` — and every evaluated event streams to your exporter as JSON-ready dicts. Exporter exceptions are caught and logged so a broken sink can never break the agent.

```python
from agentegrity.langchain import register_exporter, instrument_graph

class PrintExporter:
    async def on_session_start(self, session_id, adapter_name, profile): ...
    async def on_event(self, session_id, event):
        print(event["event_type"], event["evaluation_result"])
    async def on_session_end(self, session_id, summary): ...

register_exporter(PrintExporter())
graph = instrument_graph(my_graph)
```

This is the integration point the commercial [**`agentegrity-pro`**](https://app.cogensec.com) dashboard listens on. Deploy the pro backend with `docker compose up`, set `AGENTEGRITY_URL` and `AGENTEGRITY_TOKEN` on the agent, and the default adapter streams every session over the published exporter HTTP API — no extra package required.

### Non-Python agents (TypeScript / Bun / Node)

TypeScript agents get the same **2–3 line zero-config** DX as the Python adapters. Install the adapter that matches your framework; each one sets `AGENTEGRITY_URL` / `AGENTEGRITY_TOKEN` from env and streams events through any `SessionExporter` you register.

**Claude Agent SDK:**
```ts
import { query } from "@anthropic-ai/claude-agent-sdk";
import { hooks } from "@agentegrity/claude-sdk";
await query({ prompt: "hi", options: { hooks: hooks() } });
```

**LangChain JS:**
```ts
import { ChatAnthropic } from "@langchain/anthropic";
import { instrument } from "@agentegrity/langchain";
await new ChatAnthropic({ callbacks: [instrument()] }).invoke("hi");
```

**OpenAI Agents SDK:**
```ts
import { Runner } from "@openai/agents";
import { instrument } from "@agentegrity/openai-agents";
await instrument(new Runner()).run(agent, "hi");
```

**CrewAI TypeScript** (`@crewai-ts/core`, Node 22+):
```ts
import { crewaiEventBus } from "@crewai-ts/core";
import { instrument } from "@agentegrity/crewai";
const close = instrument(crewaiEventBus, { crew });
```

**Google ADK:**
```ts
import { instrument } from "@agentegrity/google-adk";
const close = instrument(agent);
```

**Vercel AI SDK** (TypeScript-native — no Python equivalent):
```ts
import { streamText } from "ai";
import { instrument } from "@agentegrity/vercel-ai";
await streamText({ model, prompt: "hi", experimental_telemetry: instrument() });
// AI SDK 7 dropped the tracer option: registerTelemetry(telemetry()) once at startup.
```

**Multi-agent in TypeScript (v0.8+).** The TS adapters mirror the Python multi-agent surface: `@agentegrity/client` ships `AgentTopology` / `AgentMember` / `AgentRole` / `TopologyKind` / `Evidence` types with a cross-runtime SHA-256 `contentHash()` that matches Python byte-for-byte. Each adapter declares topology at the right discovery point:

```ts
// LangGraph JS — walks graph.getGraph().nodes
import { instrumentGraph } from "@agentegrity/langchain";
instrumentGraph(compiledGraph);

// OpenAI Agents JS — seeds PEER_TO_PEER on first agent, grows on each handoff
// (no extra call: instrument(runner) already does this when used with handoffs)

// CrewAI TypeScript: the crew option walks crew.agents
import { instrument } from "@agentegrity/crewai";
instrument(crewaiEventBus, { crew });

// Google ADK JS — walks agent.subAgents (or sub_agents)
import { instrument } from "@agentegrity/google-adk";
instrument(workflowAgent);
```

Claude SDK and Vercel AI SDK stay single-agent by framework design; conformance pins the absence.

Each adapter re-exports `report()`, `reset()`, and `registerExporter()` for the same flow you get in Python. The low-level `@agentegrity/client` reporter is still available for custom frameworks, and the wire format is published as JSON Schema (`schemas/exporter/`) and OpenAPI 3.1 (`schemas/openapi.yaml`). Drift between the Python `to_dict()` output and the schemas is caught in CI by `tests/test_schemas.py`.

### Evaluate an arbitrary agent profile

For agents outside the Claude SDK — or for one-off profile scoring — the high-level `AgentegrityClient` runs the full four-layer evaluation in four lines:

```python
from agentegrity import AgentegrityClient

client = AgentegrityClient()
score = client.evaluate(client.create_profile(name="my-agent"))
print(f"{score.composite:.3f}  ({score.action})")
```

### Runtime monitoring with attestation

For long-running agents, wrap actions with `@monitor.guard` to run pre- and post-execution checks and build a tamper-evident attestation chain:

```python
from agentegrity import IntegrityMonitor

monitor = IntegrityMonitor(
    profile=client.create_profile(name="my-agent"),
    evaluator=client.evaluator,
    threshold=0.70,
    enable_attestation=True,
)

@monitor.guard
async def agent_action(context=None):
    return await agent.execute(context)

result = await agent_action(context={"action": {"type": "tool_call"}})
print(f"Records: {len(monitor.attestation_chain)}")
print(f"Chain valid: {monitor.attestation_chain.verify_chain()}")
```

### Configuring the evaluator

When the defaults aren't enough — custom thresholds, custom layer weights, custom threat detectors — drop down to `IntegrityEvaluator` directly:

```python
from agentegrity import IntegrityEvaluator
from agentegrity.layers import AdversarialLayer, CorticalLayer, GovernanceLayer

evaluator = IntegrityEvaluator(
    layers=[
        AdversarialLayer(coherence_threshold=0.85),
        CorticalLayer(drift_tolerance=0.10),
        GovernanceLayer(policy_set="enterprise-default"),
    ]
)
```

See [`examples/`](examples/) for walkthroughs including custom threat detectors, custom policy rules, and the full explicit-config Claude adapter flow.

---

## Repository Structure

```
agentegrity/
├── MANIFESTO.md                 # The Agentegrity Manifesto
├── README.md                    # You are here
├── STATUS.md                    # Maturity matrix + published benchmark numbers
├── CHANGELOG.md
├── SECURITY.md
├── GET_STARTED_AI_GUIDE.md      # Self-install guide for coding agents
├── agentegrity-glossary.md      # Vocabulary of the discipline
├── pyproject.toml
│
├── spec/                        # Framework Specification
│   ├── SPECIFICATION.md
│   ├── threat-model.md          # STRIDE on the framework itself
│   ├── adapter-quick-reference.md
│   ├── properties/              # adversarial-coherence, environmental-portability,
│   │                            # verifiable-assurance, decision-provenance,
│   │                            # multi-agent-extensions
│   └── layers/                  # adversarial, cortical, governance, recovery
│
├── src/agentegrity/             # Python Reference Implementation
│   ├── __main__.py              # CLI: info, doctor, pro, hook, verify-decisions, report
│   ├── claude.py … bedrock_agents.py  # Zero-config surfaces, one per framework
│   ├── core/
│   │   ├── profile.py           # AgentProfile (+ .default() factory)
│   │   ├── evaluator.py         # IntegrityEvaluator, AsyncIntegrityEvaluator
│   │   ├── attestation.py       # AttestationRecord, AttestationChain
│   │   ├── decision.py          # DecisionRecord (decision provenance)
│   │   ├── credentials.py       # fingerprint(), CredentialRegistry (credential provenance)
│   │   ├── tool_classifier.py   # classify_tool_call(): argument-level categories
│   │   ├── topology.py          # AgentTopology (multi-agent)
│   │   ├── approval.py          # ApprovalWorkflow (HITL)
│   │   ├── keys.py              # KeyProvider trust anchor
│   │   ├── monitor.py           # IntegrityMonitor with @guard decorator
│   │   └── telemetry.py         # Anonymous, shape-only telemetry
│   ├── layers/
│   │   ├── adversarial.py       # AdversarialLayer + ToolSequenceDetector + ToolArgumentDetector
│   │   ├── adversarial_llm.py   # Opt-in Claude-backed semantic classifier
│   │   ├── adversarial_slm.py   # Opt-in local-model classifier
│   │   ├── embedding_similarity.py
│   │   ├── cortical.py / cortical_llm.py
│   │   ├── governance.py        # GovernanceLayer (GOV-001 to GOV-005)
│   │   ├── recovery.py          # RecoveryLayer
│   │   └── checkpoint.py / kms_checkpoint.py / baseline_store.py
│   ├── adapters/                # _BaseAdapter + one adapter per framework,
│   │                            # hook_hosts.py (ClaudeCodeAdapter, CodexAdapter)
│   ├── hooks/                   # Coding-agent hook runtime
│   │   ├── protocol.py          # Host payloads to adapter events, verdicts per host
│   │   ├── session.py           # HookSession: stateful adapter + chain persistence
│   │   └── daemon.py            # Per-session daemon + fail-open hook client
│   ├── exporters/               # http.py, otel.py, alerts.py
│   └── sdk/                     # AgentegrityClient
│
├── integrations/
│   ├── claude-code/             # Claude Code plugin (hooks, /agentegrity-status)
│   └── codex/                   # Codex plugin (plugin.json, hooks)
├── .claude-plugin/              # Claude Code marketplace
├── .agents/plugins/             # Codex marketplace
│
├── schemas/                     # Cross-language wire contract
│   ├── exporter/                # JSON Schema (Draft 2020-12)
│   └── openapi.yaml             # OpenAPI 3.1 for the exporter endpoints
│
├── clients/typescript/          # @agentegrity/client + six framework packages
├── docs/                        # quickstart.md, telemetry.md
├── tests/                       # pytest suite (`pytest`; benchmarks via `-m benchmark`)
└── examples/                    # Adapter examples + exporter_receiver/
```

---

## Roadmap

**v0.1.0 — Initial release.** Three-layer architecture (adversarial / cortical / governance), pattern-based reference checks, cryptographic attestation, custom validator and policy extension points, three working examples. The recovery layer joined as a default fourth layer in v0.6.0.

**v0.2.0 — Claude Agent SDK, LLM-backed checks, and self-recovery.** First framework adapter targeting the Claude Agent SDK with five integration points (Harness, Tools, Sandbox, Session, Orchestration). Optional LLM-backed cortical checks using Claude for semantic analysis of reasoning chains, memory provenance, and behavioral drift. Recovery integrity layer for self-recovery verification. Async-first evaluator pipeline that runs independent layers in parallel.

**v0.2.1 — Developer experience.** Zero-config `agentegrity.claude` top-level module (`hooks()` / `report()` / `reset()` — three-line instrumentation with no setup), `AgentProfile.default()` factory, `python -m agentegrity` info + `doctor` self-check CLI.

**v0.3.0 — Multi-framework adapters.** Four new framework adapters joining Claude — **LangChain / LangGraph**, **OpenAI Agents SDK**, **CrewAI**, and **Google Agent Development Kit** — each with the same three-line instrumentation surface. A `_BaseAdapter` shared by all five implementations means new frameworks are mostly mechanical to add going forward.

**v0.4.0 — Exporter hook + cross-language contract.** OSS-side `SessionExporter` protocol + `register_exporter()` on every adapter. Session data (session_start, every evaluated event, session_end) streams as JSON-ready dicts to any subscribed exporter, fail-open so a broken sink never breaks the agent. The wire format is published as **JSON Schema** (`schemas/exporter/`) and **OpenAPI 3.1** (`schemas/openapi.yaml`); a first-party **TypeScript client** in `clients/typescript/` (`@agentegrity/client`) lets Bun / Node agents emit the same event stream. The commercial dashboard ships separately as `agentegrity-pro`.

**v0.5.0 — Six TypeScript framework adapters.** Mirror the five Python adapters (Claude Agent SDK, LangChain JS, OpenAI Agents SDK, CrewAI JS, Google ADK) as dedicated npm packages — `@agentegrity/claude-sdk`, `@agentegrity/langchain`, `@agentegrity/openai-agents`, `@agentegrity/crewai`, `@agentegrity/google-adk` — plus a TypeScript-native `@agentegrity/vercel-ai` adapter for the Vercel AI SDK via its OpenTelemetry tracer surface. Every package depends on a shared `createDefaultAdapter()` helper in `@agentegrity/client` and ships the same 2-3 line zero-config DX as Python. Release workflow publishes all seven packages in a matrix; `scripts/check-versions.ts` enforces version parity with `pyproject.toml`.

**v0.5.3 — Release & build polish.** Concrete version pins on TypeScript workspace deps (replacing `workspace:*`) so published packages install cleanly off‑registry, GitHub Actions bumped to checkout@v5 / setup-python@v6 / setup-node@v5, scoped push triggers + concurrency cancellation in CI, repo moved to the `cogensec` org, and an `AGENTEGRITY_OFFLINE` env var so test runs work without a reporter. Adds a Python `scripts/check_versions.py` mirroring the TypeScript one to keep `pyproject.toml`, `src/agentegrity/__init__.py`, and the README badge / claim lines from drifting apart again.

**v0.6.0 — Detection depth + recovery round-trip + conformance + benchmark.** The adversarial layer's substring match becomes a 21-pattern regex taxonomy across six attack families. The cortical layer's drift metric becomes Jensen-Shannon distance with Laplace smoothing and a `min_drift_samples` guard. `RecoveryLayer` gains a real `Checkpoint` Protocol with `InMemory` / `File` / `Sqlite` reference backends and a tested `snapshot()` ↔ `restore_to()` round-trip. The cortical layer gains a parallel `BaselineStore` Protocol so behavioural baselines survive process restarts. A cross-adapter conformance suite pins 9 invariants × 5 adapters. A detection benchmark harness (`pytest -m benchmark`) runs the synthetic suite plus loaders for InjecAgent / PINT / AgentDojo; numbers published in `STATUS.md`. Branch coverage gates land on Python (≥85%) and TypeScript (≥80% lines / 70% functions). The recovery layer is promoted to a first-class fourth default layer; `PropertyWeights` defaults rebalanced so RI gets 0.15 of the composite.

**v0.7.0 — Three new Python adapters + decision provenance.** AWS Bedrock Agents (Strands hooks with real `event.cancel_tool` enforcement + boto3 trace-stream observation surface), Agno (Agent + Team via `pre_hooks` / `post_hooks` / `tool_hooks`, real enforcement via `StopAgentRun`), and AutoGen (OpenTelemetry SpanProcessor consuming GenAI semconv spans) join the Python adapter family — now eight strong. CrewAI compat fix for the 1.x event-bus relocation. A new synchronous `_evaluate_sync` dispatch core unlocks real enforcement on sync hook surfaces. The big core addition is **decision provenance**: `DecisionRecord` lives in the same `AttestationChain` as `AttestationRecord`, captured at the three decision boundaries (`pre_tool_use` / `stop` / `subagent_start`) before the action executes, Evidence-linked back from each subsequent attestation, verifiable via `python -m agentegrity verify-decisions <chain.json>`. The `Evidence.content_hash` defect (process-salted Python `hash()`) is fixed; chains serialized pre-v0.7 fail `verify_chain()` after upgrade — re-build from a fresh root.

**v0.8.0 — Multi-agent topology.** New `AgentTopology` core type (immutable snapshots with deterministic `content_hash`) modeling the in-process multi-agent system across `HUB_SPOKE` / `HIERARCHICAL_DAG` / `PEER_TO_PEER` / `GROUP_CHAT` shapes. Every framework with multi-agent primitives (Agno teams, CrewAI crews, Bedrock collaborators, AutoGen GroupChat, Google ADK workflow agents, LangGraph supervisor/swarm, OpenAI Agents handoffs) declares its topology at the right discovery point; Claude Agent SDK stays single-agent by framework design. Topology surfaces as `Evidence(evidence_type="topology")` — no canonical-payload break. Adversarial layer extends to `shared_memory` and `broadcast_channels` with a new `peer_coercion` regex family and a peer-authority check on undeclared senders. Recovery layer adds cascade detection over `peer_score_history` (T-CASCADE) and a `peer_quarantine` capability. Cortical layer gains **per-role behavioural baselines** — `BaselineStore` keyed by `(agent_id, role)` with backward-compat fallback to pre-v0.8 entries — catching the role-drift attack (T-ROLE-DRIFT) where a compromised member starts behaving outside its declared role. Governance rule `GOV-004` now gates on topology member count instead of a synthetic action type that no adapter produced. Six new canonical events: `topology_declared`, `topology_change`, `peer_message`, `shared_memory_write`, `broadcast`, `task_started`. CrewAI semantic fix: tasks no longer map to `subagent_start` — that fires only on real `AgentExecutionStartedEvent`s now (`legacy_task_mapping=True` shim for one cycle). **Full TypeScript multi-agent parity**: `@agentegrity/client` ships `AgentTopology` / `Evidence` types with cross-runtime SHA-256 hash compatibility (TS and Python produce the same digest for structurally identical topologies); the LangChain / OpenAI Agents / CrewAI / Google ADK JS adapters declare topology from their framework's multi-agent primitives.

**v0.8.1 — Security hardening.** Closes every finding from a full adversarial audit of the v0.8.0 surface. Trust model: `AttestationChain.verify_signatures(trusted_keys=…)` adds cryptographic, trust-anchored chain verification (hash linkage alone was never tamper-evidence against an attacker who controls the serialized chain), and the `verify-decisions` CLI stops reporting unsigned chains as verified (new `--trusted-key`). Enforcement: `escalate` now fails closed under `enforce=True` via an `approval_handler`, so the built-in `REQUIRE_APPROVAL` policies actually gate. Deserialization: the embedding-similarity cache moved from `pickle` to JSON (RCE via a poisoned cache). Hardening: bounded context buffers (memory-exhaustion), exception text kept out of serialized records (info-disclosure), allow-listed storage identifiers, TLS-gated reporter token, restrictive store-file permissions, and TOCTOU-free store I/O. Plus a `dependency-audit` CI job + Dependabot. **Breaking:** the session-summary field `chain_valid` is renamed `chain_hash_linked`; see CHANGELOG for migration.

**v0.9.0 — Telemetry + injection-path hardening.** Anonymous, shape-only usage telemetry (enum values, counts, rounded scores; never prompts, tool arguments, or agent content) to guide adapter and evaluation-surface priorities. Stdlib-only sender, no new dependencies, and it can never break the host process. Opt out with `DO_NOT_TRACK=1`, `AGENTEGRITY_TELEMETRY_DISABLED=1`, or `agentegrity.disable_telemetry()`; every event is documented in [docs/telemetry.md](docs/telemetry.md). Two injection-path fixes ship alongside it: tool arguments can no longer shadow the fields governance matches on (a prompt-injected agent could name an argument `tool` and skip GOV-001's approval gate entirely), and the LLM classifier now covers `shared_memory` and `broadcast_channels`, closing the cross-agent cascade path those v0.8 channels exist to defend. LLM classification cost is bounded with concurrency and per-evaluation ceilings, plus verdict caching. **Breaking:** tool-call actions nest their arguments under `action["arguments"]`; custom `PolicyRule` conditions reading call-derived keys off the top level must be updated. See CHANGELOG for migration.

**v0.10.0 — Session export + detection hardening.** Two ways to get evaluated session data off the agent, both riding the existing `SessionExporter` seam: **OpenTelemetry** (`pip install "agentegrity[otel]"`) with one trace per session and metrics to any OTLP backend, and **direct HTTP** via a stdlib-only `HTTPExporter` plus an `agentegrity pro` CLI; attaching a sink is never silent, and `AGENTEGRITY_TOKEN` + `AGENTEGRITY_URL` (previously inert) now activate streaming. The adversarial layer closes its disclosed action-oriented-injection gap: an `action_injection` family (InjecAgent TPR 0.000 → 1.000, calibrated on that suite and labelled as in-distribution recall), a `tool_poisoning` family with a `tool_definitions` channel for MCP metadata attacks, reasoning-trace scanning, and a behavioral `ToolSequenceDetector` that flags sensitive-read → external-send tool sequences no content pattern can see. `AdversarialSLMLayer` runs the semantic classifier against any OpenAI-compatible local server (Ollama / llama.cpp / vLLM) with zero added dependencies. Distribution: two-line `agentegrity.init()` with framework auto-detection, a Claude Code plugin whose allow/ask/deny verdicts append to a verifiable per-session decision chain, and MCP-aware governance matching. Assurance: `ApprovalWorkflow` (HITL that fails closed on timeout, approvals recorded as signed provenance), webhook/Slack alert exporters, the `KeyProvider` trust anchor with **strict** cross-agent verification, and `agentegrity report` for audit reports mapped to EU AI Act / NIST AI RMF.

**v0.11.0: Coding agents, detection correctness + credential provenance.** Tool calls are classified by their arguments (`classify_tool_call()`): shell commands are tokenized the way the shell sees them and split into pipelines, so `curl … | sh`, log tampering and `$IFS` obfuscation are caught by structure, and a new `ToolArgumentDetector` blocks remote code execution and evidence tampering under enforcement. **GOV-005** escalates an external send after a secret was read anywhere in the session. Three correctness fixes: tool outputs and failures now reach the layers that scan them (they were stored under keys no layer read), scores recover according to what the evidence means (tool errors age out after 10 calls, injected content clears at compaction, behavioral sequences stay session-scoped), and hash-folded embeddings no longer depend on Python's per-process hash salt. Coding agents get a stateful hook runtime (`agentegrity hook --host claude-code|codex`, one daemon per session) with plugins for **Claude Code** and **Codex**. **Credential provenance** records a keyed fingerprint of each credential an agent declares, never the value, so a secret a scanner finds can be traced to the agent and every session that used it. **Breaking:** the Claude Code plugin's `AGENTEGRITY_CC_*` variables are renamed; see CHANGELOG.

**v0.12.0: Token usage + framework packages that attach (current).** Every adapter reports the LLM tokens a session spends, normalized so `input_tokens` always includes cached tokens, totalled per model and keyed per model call so repeats count once: both coding-agent hosts read their transcripts, all eight Python adapters and all six TypeScript packages read their framework's usage. The TypeScript packages are rebuilt against their frameworks' real lifecycle APIs and tested against the frameworks themselves; four of them never received events before. Drift and sustained degradation are measured for the first time, and drift past twice the tolerance alerts instead of blocking.

**Unreleased on `main` — Runtime Action Authority and evidence reconciliation.** The
Python OSS implementation adds task-bound execution authorization, trusted-host
source provenance for protected arguments, bounded subagent delegation, and
late-arriving downstream evidence reconciliation. These are opt-in and
process-local; see the [runtime-security overview](#runtime-action-authority-opt-in-python-oss)
and the individual guides below. This entry describes merged source changes,
**not a new package release or independently measured prevention rate**.

**Federation + fleet (next).** `FederationLayer` at pipeline position 0 producing topology context the existing layers consume. `Coordination Integrity` property at default weight 0.0 (rebalanced to ~0.10-0.15 in a follow-up). `KeyProvider` Protocol with multi-agent signatures + per-agent attestation chains (pattern b). Fleet aggregator for population posture formalizing the glossary's "Agentegrity Posture." Deliberately unnumbered: it has slipped a version twice already, and pinning it just forces a roadmap edit every time an unplanned release lands.

**v1.0.0 — Stable API (when ready).** Declared stable when the public API has been unchanged for a full minor release cycle, when the library has production deployments at three or more external organizations, and when the framework has been cited in at least one peer-reviewed publication. v1.0.0 is not a date — it's a signal that adoption has happened beyond our direct influence.

---

## Documentation

| Document | Description |
|---|---|
| [Manifesto](MANIFESTO.md) | The founding statement of agentegrity as a discipline |
| [Specification](spec/SPECIFICATION.md) | Full technical specification (properties, layers, controls, scoring) |
| [Glossary](agentegrity-glossary.md) | Vocabulary of the discipline, defined precisely |
| [Adversarial Layer](spec/layers/adversarial-layer.md) | Self-defense verification architecture |
| [Cortical Layer](spec/layers/cortical-layer.md) | Self-stability verification architecture |
| [Governance Layer](spec/layers/governance-layer.md) | Policy enforcement and audit architecture |
| [Recovery Layer](spec/layers/recovery-layer.md) | Self-recovery verification architecture |
| [Runtime Action Authority](docs/RUNTIME-ACTION-AUTHORITY.md) | Time-bound mandates, declared tool effects, and execution-boundary checks |
| [Trust Boundaries and Data-as-Control](docs/TRUST-BOUNDARIES-AND-DATA-CONTROL.md) | Host-supplied provenance and protected argument authority |
| [Cascading Delegation](docs/CASCADING-DELEGATION-AUTHORITY.md) | In-process scope attenuation, subagent identity and revocation |
| [Late-Arriving Effect Evidence](docs/LATE-ARRIVING-EFFECT-EVIDENCE.md) | Receipt uncertainty, delayed verification, conflict handling and deadlines |
| [Authorization & Evidence Example](examples/action_authority_evidence.py) | Executable, offline demonstration (synthetic ledger) |
| [Telemetry](docs/telemetry.md) | Anonymous usage analytics: every event documented, opt-out |

---

## Design Principles

1. **Self-securing capability is the goal. Verification is the methodology.** The framework exists because agents need to be able to secure themselves. The scoring system is how we prove they can. Without the underlying capability, the score is theater. Without the verification methodology, the capability is unprovable. Both are required.

2. **Composition layer, not model layer.** Better base models do not eliminate the need for agent-level verification. They make compositions more capable and therefore more dangerous when compromised. The framework is positioned at the composition layer specifically because that's the layer model improvements don't close.

3. **Defense-in-depth, not defense-in-replacement.** Guardrails, runtime monitors, and network controls remain essential. Agentegrity adds a layer that sits inside the agent's decision process where exogenous controls cannot reach. The two complement each other.

4. **Cryptographic, not observational.** "We monitored the agent and it looked fine" is not assurance. Attestation records produced by this library are signed, chained, and independently verifiable. Verification means you can prove what the agent's state was at a point in time, not just that someone watched it.

5. **Open standard, plural implementations.** The specification is open. The reference implementation is Apache 2.0. Other implementations are welcome from any vendor, any framework, any deployment context. The integrity of autonomous agents is too important to be proprietary, and a single-vendor standard isn't a standard.

6. **Honest about limitations.** Every claim the library makes is defensible in writing. When checks can't run, they say so. When the implementation is a pattern-based reference rather than semantic analysis, the README says so. The worst possible outcome for this project is a published benchmark showing that our claims are louder than our implementation. We avoid that outcome by being the first to name limitations.

---

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Priority areas for future contributions:
- Reproducible, independent adversarial evaluations of authorization and recovery
- Authenticated downstream-effect verification and durable evidence/replay stores
- Distributed subagent delegation, revocation and agent identity integration
- Domain-specific tool authority policies and validators (finance, healthcare, embodied agents)
- Formal verification of enforcement and reconciliation state transitions
- Cross-framework session merging and trustworthy multi-agent evidence

---

## Citation

If you use the Agentegrity Framework in research or production, please cite:

```bibtex
@misc{agentegrity2026,
  title={The Agentegrity Framework: Building and Verifying Self-Securing Autonomous AI Agents},
  author={Cogensec Research},
  year={2026},
  url={https://github.com/cogensec/agentegrity}
}
```

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

---

**Agentegrity is a Cogensec Research initiative.** The discipline is open. The framework is open. The code is open. We invite researchers, practitioners, and organizations building or deploying autonomous AI agents to adopt, implement, extend, and critique it.


