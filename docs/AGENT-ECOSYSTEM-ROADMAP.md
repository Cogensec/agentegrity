# Agent Ecosystem Integration Roadmap

**Status:** Proposed engineering roadmap — not a list of shipped integrations.
**Scope:** Security and integrity of **all autonomous AI agents** across digital and physical environments. Coding agents are one category, not the product boundary.
**Baseline:** Reviewed against the Agentegrity source tree at v0.12.0 / the v0.13.0 release candidate.

## Product thesis

Agentegrity is the agent-integrity layer for *autonomous systems that reason, use tools, delegate, or affect their environment*. Framework and harness integrations are ways to reach those systems, not the definition of the market.

Our goal is to make the same fundamental questions answerable across domains:

1. **What did this agent observe, decide, and attempt?**
2. **Was it authorized by a trusted principal to make that decision and perform that action?**
3. **Could an installed execution control actually stop an unauthorized side effect?**
4. **What independently authenticated evidence establishes the outcome?**
5. **What state was recovered, and what evidence was retained after failure?**

The answer must remain honest when the host, framework, or tool has no accessible telemetry, veto, or recovery interface.

## Four capability levels — never collapse them into one "supported" badge

| Level | Capability | Minimum proof required |
| --- | --- | --- |
| **O — Observe** | Capture authenticated or explicitly source-labeled lifecycle/tool/usage events | Events with correct source, coverage declaration, no silent collection |
| **E — Evaluate** | Normalize events into Agentegrity's integrity evaluation and preserve available decisions | Reproducible layer outcomes and accurate provenance/signature reporting |
| **A — Authorize** | Apply a trusted principal's task/resource/effect policy at a *real* execution boundary | Negative test proving a denied action never reached the executor |
| **V — Verify / Recover** | Correlate externally authenticated effects and validate recovery continuity | A real downstream verifier, explicit unknown/conflict semantics, checkpoint evidence |

An O-level transcript import is not an A-level enforcement integration. A signed in-process event does not prove that an external effect committed. Recovery "support" requires an attached, tested recovery mechanism.

## Ecosystem map

Coverage priorities below are **product and engineering candidates**, not claims about actual vendor integration compatibility. Confirm publicly available APIs, supported editions, partner agreements, and host version before implementing.

| Agent category | Example environments and use cases | Primary attack surfaces | Likely integration | Initial target |
| --- | --- | --- | --- | --- |
| **Framework-built / custom agents** | LangGraph, OpenAI Agents, Google ADK, CrewAI, AutoGen, Agno, AWS Strands; custom Python/TS agents | Prompt/tool injection, inter-agent handoffs, sensitive egress | Python/TS SDKs, framework callbacks, trusted tool wrappers | **O/E**, **A** on verified host surfaces |
| **Enterprise workflow agents** | Microsoft Copilot Studio, Salesforce Agentforce, ServiceNow, internal workflow platforms | Identity/role delegation, enterprise data access, SaaS actions | Tenant-authorized connector, workflow action gateway, events/API | **O**, **A** at customer-owned action API |
| **Financial and transaction agents** | Banking, payment preparation, treasury, underwriting, insurance, trading operations | Transaction authorization, account/resource substitution, late settlement | Task mandates at payment/tool gateway, signed host audit, bank/system-of-record verifier | **A/V** for synthetic then authorized pilots |
| **Customer-facing and commerce agents** | Support bots, sales agents, booking, CRM, ordering | PII leakage, refunds, account takeover, unsafe account changes | Framework SDK + customer-owned API gateway | **O/E/A** for sensitive actions |
| **Browser and computer-use agents** | Browser navigation, forms, purchasing, file uploads, desktop workflows | Web-to-tool injection, unauthorized navigation/actions, credential misuse | Browser/tool-execution proxy, permission events, native extension with consent | **O/E**; **A** only at controllable action boundary |
| **Research and analytical agents** | Research synthesis, retrieval, scientific workflows, OSINT, analysis assistants | Retrieval injection, source laundering, tool-result poisoning, ungrounded conclusions | SDK instrumentation, retrieval provenance, evidence export | **O/E** + protected-parameter provenance |
| **Infrastructure / security agents** | SOC triage, incident response, IAM, cloud operations, DevOps | Privileged action, command execution, lateral propagation, log/evidence tampering | Tool gateway, SIEM/OTel observation, IAM policy adapter | **O/E/A**, side-effect reconciliation |
| **Multi-agent orchestration** | Swarms, supervisors, task delegators, message-passing networks | Privilege amplification, poisoned shared state, cascading failures | Topology/identity signals + bounded delegation + host enforcement | **E/A**, in-process then distributed designs |
| **Coding and development agents** | Claude Code, Codex, Cursor, OpenCode, Gemini CLI, Copilot CLI | Shell/file access, tool misuse, prompt injection | Native tool lifecycle hooks and plugins | Existing **O/E**, expand host-specific **A** |
| **Robotics and embodied agents** | Industrial robotics, smart devices, autonomous inspection, physical AI | Unsafe actuators, sensor spoofing, physical effects, emergency stops | ROS2 telemetry and approved safety-controller interface; hardware attestation research | **Research only** until safety-certified integration and real-world validation |

**Explicit exclusions:** Do not imply blanket support for every Copilot, Agentforce, ServiceNow, browser, robotics, or cloud deployment. No physical safety guarantee follows from an SDK integration.

## Existing Agentegrity building blocks (as of the release candidate)

- **Eight Python framework adapters:** Claude Agent SDK, LangChain/LangGraph, OpenAI Agents SDK, CrewAI, Google ADK, AutoGen, Agno, AWS Bedrock/Strands. Python integrations provide the four evaluation layers and an attestation chain where wired.
- **Six TypeScript framework packages:** Claude SDK, LangChain, OpenAI Agents, CrewAI, Google ADK, Vercel AI SDK. TypeScript packages **stream events**; they do not run the Python evaluator or sign a local Python chain.
- **Coding-agent hooks:** Claude Code and Codex use `src/agentegrity/hooks/protocol.py`, `session.py`, `daemon.py`, and `adapters/hook_hosts.py`. No other coding harness is registered today.
- **Exporters:** The `OTelSessionExporter` **sends Agentegrity sessions outward**; it is not an existing universal inbound OTLP receiver. HTTP session export likewise does not provide remote tool veto.
- **New experimental Python source APIs:** `TaskMandate`, `ToolAuthority`, `ActionSecurityGate`, `ActionProvenance`, delegation and effect receipts. Host-owned authority and independently authenticated verification are required. Processes do not share delegated authority or receipts by default.
- **Known hook limitation:** A daemon timeout can be fail-open; in-process fallback may lose cross-call state. Do not promise complete enforcement coverage based on the host name alone.

## Architecture to build

```text
 Enterprise  Finance  Customer  Browser  Research  Infrastructure  Multi-Agent  Coding  Physical
     │        │         │         │         │            │             │         │       │
     └────────┴─────────┴─────────┴─────────┴────────────┴─────────────┴─────────┴───────┘
                                HOST / FRAMEWORK ADAPTERS
                       native hooks · SDKs · tool gateways · OTel
                                           │
                               CANONICAL AGENT EVENT
                     source identity / session / action / trust labels
                                           │
                     ┌─────────────────────┴──────────────────────┐
                     │                                            │
              INTEGRITY EVALUATOR                         TRUSTED AUTHORITY
                 detection, drift                         mandate / action policy
                 decision evidence                      at actual execution boundary
                     │                                            │
                 event trail                              allow / deny / escalate
                     │                                            │
                     └─────────────────────┬──────────────────────┘
                                           │
                                EFFECT AND RECOVERY EVIDENCE
                      verified outcome · unknown · conflict · restore
```

This is a **target architecture**, not a depiction of a completed universal collector. Separate host-asserted identity from authenticated principal/resource identity. Preserve data origin and coverage gaps across every bridge.

### Cross-domain contracts

1. **Canonical event contract (O):** Versioned `AgentEvent` schema with host/framework, agent/run/session identifiers, event type, timestamp, tool/action IDs, scoped payload, optional usage, provenance trust level, and event-source authentication status. Preserve opaque provider IDs; never manufacture a verified identity from user-controlled text.
2. **Trust and policy contract (A):** A principal-signed or trusted-host-issued task mandate specifying time, resource, effect, tool and credential scope; integrate at an enforceable execution boundary. Explicitly fail or label missing host capabilities.
3. **Evaluation contract (E):** Map host events to observed integrity signals without pretending missing reasoning, memory or handoff data were seen. TypeScript clients may export for central evaluation; they do not secretly run Python semantics.
4. **Evidence contract (V):** Bind an outcome to principal, run, tool, resource, effect and downstream service response; distinguish pending/unknown/confirmed/failed/conflicted. Make reconciliation durable *before* claiming distributed recovery.
5. **Privacy contract:** Require opt-in content capture, payload caps, secret redaction, destination disclosure, retention policy, and isolated tenants/hosts. Avoid collecting prompts or financial/customer data by default for generic observation.
6. **Safety contract for embodied systems:** Defer enforcement on real actuators to certified/approved safety controllers and interlocks. Start with isolated simulation, hardware-in-loop validation and independent safety review.

## Prioritized engineering work — balanced across agent categories

| Workstream | Priority | Deliverable | Acceptance criteria |
| --- | --- | --- | --- |
| **U1 Universal event + capability model** | P0 | Canonical events, action scopes, source trust tags, capability matrix, conformance fixtures | Same fixture semantics across Python/TypeScript; no default behavior change |
| **U2 General tool/action authority gateway** | P0 | Framework-independent trusted executor wrapper and adapters for customer-owned API/tool boundaries | Unauthorized first-call effect stopped in a synthetic enterprise + financial action |
| **U3 Financial agent reference evaluation** | P0 | Paired synthetic banking evaluation across 10 scenarios × 10 runs, with evidence and false-positive rates | Deterministic seeds, paired baselines, published methodology; do **not** announce metrics before execution |
| **U4 Enterprise + customer agent proof of concept** | P0 | One *approved* enterprise workflow and one support/commerce agent integrating the gateway | Data boundaries, denial and audit semantics, real SDK/version compatibility verified |
| **U5 Coding harness extensions** | P1 | Cursor + OpenCode first; Gemini CLI / Copilot CLI next | Real-host smoke tests for pretool veto, tool failure, native hooks and host version drift |
| **U6 Browser/computer-use boundary** | P1 | Controllable browser action proxy and trustworthy page/tool provenance | Web content cannot issue approved protected actions; no claim of universal browser interception |
| **U7 Infrastructure/SOC reference workflow** | P1 | IAM/cloud/SOC read-to-write escalation protection and signed evidence export | Privileged-action denial + replay/rollback tests |
| **U8 Distributed multi-agent authority** | P1 | Cross-process identity, attenuated grants, revocation propagation and durable receipts | Threat-modeled distributed replay and partition tests |
| **U9 Research + provenance reference** | P1 | Retrieval/source trust and protected-parameter evaluations | Injection/data-as-control datasets, measurable error and miss cases |
| **U10 Physical/robotics research track** | P2 | Simulation safety boundaries and controller integration requirements | No real actuation before domain safety and independent validation |
| **U11 Pro ecosystem visibility** | P2 | Native capability-level ingestion/identity/verification dashboard | "Observed" vs "enforced" vs "verified" clearly distinguished |

**Sequencing:** Start with U1 + U2, evaluate on U3, and exercise the same policy across U4. Build the coding harness breadth in parallel, but do not allow it to define the universal data model.

## Coding-agent harness track (a subset of U5)

| Candidate | Native integration approach to validate | Initial scope | Required proof |
| --- | --- | --- | --- |
| Cursor | Native tool/command hooks, with cloud-specific limitations | O/E; A only where host veto works | Deny synthetic file/shell/MCP effects before execution |
| OpenCode | TypeScript lifecycle plugin and local evaluator bridge | O/E; conditional A | Before-tool cancellation and plugin failure semantics |
| Gemini CLI | `BeforeTool` / `AfterTool` hooks | O/E; conditional A | Documented deny response in pinned host version |
| GitHub Copilot CLI | `preToolUse` / `postToolUse` hooks | O/E; conditional A | CLI-specific veto and approval-flow tests |
| OpenClaw Gateway | Tool-hook plugin | O/E; conditional A | Confirm gateway pretool hook really prevents effects |
| Pi / Cline / Kiro / Qwen Code | Extension or host-specific lifecycle hooks | O first | Source authenticity, privacy, failure behavior |

Do not assume identical host hook payloads, approval semantics or enforcement guarantees. Existing Claude Code/Codex handling may fail open on daemon timeout. A generic client reporter is not an enforcement engine.

Useful references: [Cursor hooks](https://docs.cursor.com/context/hook-features), [OpenCode plugins](https://opencode.ai/docs/plugins/), [Gemini CLI hooks](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md), [Copilot CLI hooks](https://docs.github.com/en/copilot/reference/hooks-reference), and [Beacon's host matrix](https://github.com/Asymptote-Labs/agent-beacon#supported-agents). Verify host APIs at implementation time.

## Independent verification requirements

Every new integration should ship a capability card showing which events were *actually* captured and which actions were *actually* denied.

- **Integration truth:** Real host and supported version, local/cloud edition, installation and uninstall flow, consent model.
- **Event coverage:** Session, prompt, model output, tool intent/result/failure, MCP, delegation, tokens, identity, provenance — with missing fields explicitly marked.
- **Enforcement:** Synthetic unauthorized effect never reaches the executor; timeouts/crashes and attempts to bypass hooks receive documented outcomes.
- **Evidence:** Claim without independent downstream attestation remains unverified; delayed/conflicting signals remain distinguishable.
- **Privacy:** Prompt/tool content handling, token or secret exclusion, retention, encryption, host isolation.
- **Recovery:** Tested checkpoint and rollback semantics; never delete compromise evidence.
- **Reliability:** Race conditions, cancellation, duplicate/replayed events, process restart, multi-host correlation.
- **Distribution:** Pinned SDK/host versions, conformance CI, docs and accurate capability matrix.

## What not to claim

Do not say "all agents supported" merely because this is the product mission. Do not call telemetry-only adapters an enforcement layer. Do not describe experimental in-process delegation or effect receipts as distributed and durable. Do not imply physical safety certification or real banking validation based on synthetic tests.

The intended trajectory is **universal agent integrity**, implemented through independently verified integrations and progressively stronger, domain-appropriate trust boundaries.
