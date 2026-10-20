# Harness expansion: technical assessment and proposed roadmap

**Status:** Proposal only. No additional harness is implemented by this document.
**Context:** Compared the Agentegrity repository with the [Agent Beacon](https://github.com/Asymptote-Labs/agent-beacon) repository and host integration documentation in October 2026.

## Distinguish three meanings of "support"

| Level | Description | What we can honestly report |
| --- | --- | --- |
| **Observe** | Receive host lifecycle events, tool calls, outputs, and optional usage through native hooks, plugins, or explicitly configured telemetry | *Instrumented* (never imply prevention) |
| **Evaluate** | Convert events to a trusted canonical event stream and feed available data into Agentegrity's four Python evaluation layers and attestation chain | *Evaluated*, with stated event gaps, host trust and signing configuration |
| **Enforce** | A trusted pre-execution hook can actually cancel/deny before effects; integrate a host-owned policy and test the veto surface | *Enforceable tool scope*, not all possible operations or automatic effect verification |

These are separate capabilities. An endpoint collector or transcript parser can observe activity but cannot retroactively prevent the command. A model response is not a trusted policy controller. True effect reconciliation additionally requires authenticated downstream verification, independently of host event capture.

## What is already reusable in Agentegrity

- **Python SDK adapters:** 8 framework-specific integrations in `src/agentegrity/adapters/`. They have richer integrity evaluation than TypeScript's event streaming.
- **TypeScript:** `@agentegrity/client` provides canonical events and a reporter; six framework packages normalize their own lifecycle surfaces. There is no local Python-equivalent integrity evaluator in those packages.
- **Coding-agent host runtime:** `src/agentegrity/hooks/protocol.py` defines supported host names, shared event normalization and decision output; `src/agentegrity/adapters/hook_hosts.py` registers only `claude-code` and `codex`. `src/agentegrity/hooks/session.py` holds per-session evaluation; `daemon.py` manages Unix-socket state with a fallback.
- **Existing observability:** `OTelSessionExporter` emits *Agentegrity sessions outward*. It is **not** a general-purpose OTLP receiver for arbitrary agent hosts. `HTTPExporter` sends session events to the configured backend. Neither can intercept tools in another harness by itself.
- **Security limitation to address before broad enforcement:** the coding-agent daemon path can fail open when it times out or cannot start. In-process fallback can also lose cross-call state. Do not claim fail-closed coverage for every host and failure mode.

## Suggested first wave of host integrations

The following columns describe an **evaluation based on host API documentation**, not Agentegrity features available now. Exact event and cancellation semantics must be validated on pinned host versions.

| Priority | Harness | Available integration strategy | Proposed initial capability | Key verification |
| --- | --- | --- | --- | --- |
| P0 | **Cursor (local and cloud agent)** | Project/user `hooks.json` with `preToolUse`, `postToolUse`, failure, prompt, shell and MCP events | Host hook normalization → Python evaluator; opt-in pretool deny | Confirm deny before shell/write/MCP effects; verify Cloud Agent hook support independently |
| P0 | **OpenCode** | TypeScript plugin `tool.execute.before/after`, session and permission events | TS plugin event bridge → local collector/Python evaluator; explore pretool enforcement | Test tool-argument mutation, cancellation, MCP coverage, and plugin failure paths |
| P0 | **Gemini CLI** | Native `BeforeTool`/`AfterTool` and other lifecycle hooks | Command-hook integration and event normalization; conditional pretool deny | Verify native `decision: deny` behavior with shell and MCP tools |
| P0 | **GitHub Copilot CLI** | `preToolUse`, `postToolUse`, lifecycle hooks | Python host integration with explicit policy and tests | Validate CLI and cloud surface separately: some permission hooks differ |
| P1 | **OpenClaw Gateway** | Typed plugin `before_tool_call`/`after_tool_call` hooks | TS plugin with optional external evaluator bridge | Plugin tool-hook veto; errors; Gateway vs agent identity; permission boundary |
| P1 | **Pi / Oh My Pi** | Extension API | TS extension emitting normalized event types | Confirm pretool restrictions and usage capture independently |
| P1 | **Cline / Kiro / Qwen Code** | Host-specific plugin or command hooks | Observation first, enforcement only after veto tests | Per-host event and privacy coverage |
| P2 | **Browser chat, IDE views, unattended cloud agents** | Optional browser extension, native runtime telemetry, or environment-local hooks | Consent-driven observation (no security enforcement claim) | Privacy, permissions, no secret collection, isolated per-session identity |

### Host API references to validate

- [Cursor hook documentation](https://prod.cursor.com/docs/hooks) and [cloud-agent hooks](https://prod.cursor.com/help/ai-features/cloud-agents)
- [OpenCode plugin hooks](https://opencode.ai/docs/plugins/)
- [Gemini CLI hook reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md)
- [GitHub Copilot hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference)
- [OpenClaw tool policy hooks](https://docs.openclaw.ai/plugins/hooks/tool-policy)
- [Beacon agent support](https://github.com/Asymptote-Labs/agent-beacon#supported-agents) and [Beacon architecture](https://github.com/Asymptote-Labs/agent-beacon/blob/main/docs/architecture/architecture.mdx)

## Recommended engineering structure

Do **not** copy the Claude/Codex JSON schema into every new host.

1. **Canonical host event contract.** Define a bounded, versioned `HostEvent` with host/session/agent/run/tool IDs, event timestamp, event type, source-trust class, content/privacy flags, and explicitly optional tool payload and token usage. Distinguish host asserted vs cryptographically trusted IDs. Bound bytes and redact secrets before storage or export.
2. **Per-host normalizer and verdict renderer.** Each integration translates native hook payloads into `HostEvent` and converts a host-independent decision into the host's *documented* deny or continue response. No renderer should emit an undocumented response shape.
3. **Three adapters per host only when justified.** Use command hooks for hosts that support them, native TS plugins for hosts that expose richer APIs, and OTel/explicit log readers for observational surfaces. A plugin should use the same semantic core rather than bypassing it.
4. **Local collection / optional central evaluation.** Consider a secure loopback-only ingest endpoint or bounded local queue to avoid Python process startup on every hook and preserve order across hosts. Authenticated clients, consent, payload caps, spool durability, multi-user isolation, and data retention are required. This is additional engineering, **not an existing collector**.
5. **Explicit enforcement posture.** Offer distinct observe/alert/enforce modes per capability and host. A timeout or unavailable verifier needs a documented configurable policy; for high-impact irreversible tools, favor explicit host denial over claiming a completed check. Distinguish host rule evaluation failures from downstream effect uncertainty.
6. **Unified installer, later.** Only after individual plugins are validated, ship opt-in user/project installation and `agentegrity doctor --host X` checks. Never silently change a host's hooks or exfiltrate session content.
7. **Hosted/Pro visibility.** Send host/source/version and enforcement-capability metadata in the existing export contract only after schema compatibility, auth, privacy and negative tests. Do not represent imported untrusted transcript data as signed local attestation.

## Proposed incremental PR sequence (not yet implemented)

| PR | Deliverable | Release gate |
| --- | --- | --- |
| **H1** | Canonical `HostEvent` model, parser fixtures, capability registry; preserve Claude/Codex behavior | Regression suite, payload and privacy fuzzing, no changed default enforcement |
| **H2** | Cursor hooks plugin + tests | Actual host execution test: deny occurs before effects; verify read, write, shell, MCP, failure and cloud limits |
| **H3** | OpenCode plugin using `@agentegrity/client` plus trusted evaluator bridge | Run realistic host tool tests; confirm passive vs blocking behaviors |
| **H4** | Gemini CLI and Copilot CLI command-hook adapters | Test each documented response shape and model/provider version against pinned releases |
| **H5** | Local OTLP/event ingestion or secure spool for passive telemetry | Threat model, replay resistance, redaction, backpressure, isolation and TLS/bind controls |
| **H6** | OpenClaw/Pi and additional host integrations | Contract conformance, no silent host-version drift |
| **H7** | Installer/doctor, support matrix, migration docs and Pro metadata | End-to-end install/uninstall, consent, rollback and interoperability |

## Test matrix required before marking any harness "supported"

- Can we capture: session start/end, user prompt, pre/post tool request, denied action, tool error, handoff, file changes, MCP invocation, token usage?
- Which hooks are observational versus cancellable? Does a denial **actually prevent** a synthetic filesystem or external side effect?
- How does a host behave when the hook process exits with an error, hangs, emits malformed JSON, or times out? Can an agent bypass instrumentation via built-ins or remote/server-side tools?
- Can session IDs be forged? Are resource, agent and run identities bound by a trusted host? Do forwarded events contain credential values, paths, prompts, or tool outputs?
- Does the package work on macOS/Linux/Windows, pinned host versions and managed/cloud variants? Do the installation and removal flows preserve user hooks?
- Are event ordering, token accounting, state restoration, replay deduplication and bounded data retention correct?
- Are all claims in the capability matrix backed by automated conformance tests and, for veto hooks, real-host smoke tests?

**Recommendation:** Build H1 and Cursor (H2) first, with OpenCode (H3) immediately after. Do not advertise Beacon-equivalent coverage until the host-level checks exist. Quantify *verified enforcement coverage* separately from the number of compatible harnesses.
