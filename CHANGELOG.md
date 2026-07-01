# Changelog

All notable changes to the Agentegrity Framework are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0 minor versions may contain breaking changes; the project remains
in beta until the v1.0 stability criteria documented in
[README → Roadmap](README.md#roadmap) are met.

## [Unreleased]

### Security
- **Signature-aware chain verification.** `AttestationChain.verify_chain()`
  only checks the (unkeyed SHA-256) hash linkage between records, which an
  attacker who controls the serialized chain can recompute — it is not
  tamper-evidence against an adversary. Added
  `AttestationChain.verify_signatures(trusted_keys=...)`, which verifies
  every record's Ed25519 signature and, when a pinned key set is supplied,
  rejects records that self-vouch with an attacker-embedded `public_key`.
  The `verify-decisions` CLI now reports signature status, accepts
  `--trusted-key` to pin a key, prints unsigned records as `unsigned`
  (previously shown as `verified: yes`), and exits non-zero unless
  signatures verify.
- **Enforcement gates on `escalate`, not only `block`.** With `enforce=True`
  the built-in governance `REQUIRE_APPROVAL` policies (high-risk tool,
  code-execution boundary, financial threshold, multi-agent), cortical
  drift, and recovery chain-tamper all emit `escalate`, which the
  enforcement path previously ignored — so "require approval" silently
  proceeded. `_BaseAdapter` and `IntegrityMonitor` now take an
  `approval_handler`: under enforcement `block` always denies and
  `escalate` denies unless the handler approves it (absent handler ⇒ fail
  closed; a raising handler ⇒ deny).
- **Embedding-similarity cache moved from `pickle` to JSON.** A
  filesystem-write attacker (threat-model T-T2) could poison a pickle cache
  into arbitrary code execution on load; JSON deserializes to inert data.
- **Bounded context buffers.** Every accumulating adapter buffer
  (`tool_calls`, `tool_outputs`, `tool_failures`, `inputs`, `subagents`,
  `peer_messages`, `shared_memory`, `broadcast_messages`, `tasks`) is now
  capped at `_BUFFER_CAP` (1000) per session via a shared `_append_capped`
  helper. Previously only broadcasts were capped, so a malicious peer/tool
  could flood the buffers and exhaust memory (and bloat exported payloads).
  Overflow emits a one-time `<channel>_overflow` event per channel.
- **Exception text no longer leaks into serialized records.** The exported
  `capture_failure` event dropped its raw `str(exc)` summary (kept
  `exception_class`), and a crashing governance rule's `reason` (which lands
  in the audit log and attestation `layer_states`) now carries only the
  exception class name. Exception messages can embed tokens / URLs / PII;
  full detail still goes to the local operator log.
- **TLS-gated Bearer token in the TypeScript reporter.** The
  `Authorization: Bearer` header is now attached only over a
  credential-safe transport (HTTPS, or a loopback host); a misconfigured
  `http://<remote>` `baseUrl` no longer leaks the token in cleartext. The
  reporter warns once at construction when an `apiKey` is set on an unsafe
  URL and withholds the header at request time (events still send).
- **Dependency CVE scanning in CI.** Added a `dependency-audit` job
  (`pip-audit` + `bun audit`, advisory) and a Dependabot config
  (`.github/dependabot.yml`) covering pip, npm, and github-actions. The
  enforcing/remediation path is Dependabot; the CI job is per-PR
  visibility (non-blocking, since a full-environment audit would otherwise
  fail `main` on ambient/transitive advisories with no project-side fix).

- **Restrictive permissions on store files (shared-host hardening).**
  `FileCheckpoint` / `FileBaselineStore` create a freshly-made store
  directory as owner-only (0700) instead of the umask default (0755), and
  `SqliteCheckpoint` / `SqliteBaselineStore` tighten their DB file to 0600
  (sqlite creates it world-readable). Pre-existing directories are left as
  the operator set them; all changes are best-effort. (The JSON backends
  already wrote 0600 files via `NamedTemporaryFile`.)
- **Race-free store reads/deletes.** `FileCheckpoint.load`,
  `FileBaselineStore.load`, and `FileBaselineStore.delete` now read/unlink
  and catch `FileNotFoundError` instead of `exists()`-then-act, removing a
  TOCTOU window.
- **Allow-list validation for filesystem-bound identifiers.**
  `FileCheckpoint` (`checkpoint_id`) and `FileBaselineStore` (`agent_id`,
  `role`) replaced their `/`,`\`,`..` block-list with a shared
  `validate_storage_identifier` allow-list (non-empty ASCII alphanumeric /
  underscore / hyphen). Closes empty-id collapse to `.json`, dotfiles, NUL
  bytes, and platform-reserved names. `FileBaselineStore` also rejects
  `__` in `agent_id`/`role` so the role-key separator stays unambiguous
  (previously `agent="a"` + `role="b"` could collide on disk with
  `agent="a__b"` + no role — a baseline-misattribution vector).

### Added
- **`GovernanceLayer(sensitive_tools=...)`** (and per-call
  `context["sensitive_tools"]`) to extend the GOV-001 high-risk-tool gate.
  The built-in set is a documented starting point, not exhaustive; matching
  is exact-string, so operators must enumerate their own tool names
  (including framework-namespaced variants). Exposed as
  `DEFAULT_SENSITIVE_TOOLS`.

### Changed
- **BREAKING: session-summary field `chain_valid` renamed to
  `chain_hash_linked`** across the Python adapters, the TypeScript client,
  and the exporter JSON Schema (`schemas/exporter/common.json`). The old
  name overstated the guarantee — the value reflects hash linkage
  (`verify_chain()`), not cryptographic validity. Consumers of the exporter
  HTTP API and `get_summary()` must update the field name.

## [0.8.0] - 2026-06-08

### Added
- **Per-role behavioural baselines in `CorticalLayer`.** Catches the
  role-drift attack where a compromised member acting in one
  declared role (e.g., `data_extractor`) starts behaving like
  another (e.g., `task_planner`). `BaselineStore` Protocol grows an
  optional `role: str | None = None` parameter on `save` / `load` /
  `delete` plus a new `list_keys()` method returning
  `list[tuple[agent_id, role|None]]`. `CorticalLayer.evaluate` reads
  the role from `context["topology_context"]["role"]` (populated by
  team-aware adapters via `set_topology`). Backward-compatible:
  pre-v0.8 baselines (saved without a role) silently serve
  role-keyed lookups via `BaselineStore`-side fallback until a
  role-specific entry is written. SQLite backend migrates the
  pre-v0.8 schema in place on first v0.8 open. T-ROLE-DRIFT
  mitigation in the threat model.
- **TypeScript multi-agent parity** with the Python adapter
  surface. `@agentegrity/client` ships `AgentTopology` /
  `AgentMember` / `AgentRole` / `TopologyKind` / `TopologyChange`
  immutable types plus `Evidence` / `EvidenceType` mirroring the
  Python core. `DefaultAdapter.setTopology(topology, myRole?)` is
  the canonical entry point — first call emits `topology_declared`,
  subsequent structurally-distinct calls emit `topology_change`
  with a `TopologyChange` diff. Cross-runtime SHA-256
  `contentHash()` matches Python byte-for-byte (validated via
  subprocess test). Six new canonical events in the `EventType`
  union: `topology_declared`, `topology_change`, `peer_message`,
  `shared_memory_write`, `broadcast`, `task_started`. Plus
  `subagent_orphan` (T-ORPHAN-LIFECYCLE event). Four framework
  adapters declare topology at the right discovery point:
  `@agentegrity/langchain` (`instrumentGraph(graph)` walks
  `graph.getGraph().nodes`), `@agentegrity/openai-agents`
  (`onAgentStart` seeds PEER_TO_PEER; `onHandoff` grows
  incrementally), `@agentegrity/crewai` (`instrument({ crew })`
  walks `crew.agents`), `@agentegrity/google-adk` (`instrument(agent)`
  walks `agent.subAgents`). Claude SDK and Vercel AI SDK stay
  single-agent by framework design (pinning tests assert no
  topology declaration). TS conformance suite gains two
  multi-agent invariants: single-agent adapters MUST NOT declare a
  topology; multi-agent-capable adapters MUST expose `setTopology`.
  Test count: 117 (52 cross-package conformance + 65 per-package).
- **Multi-agent topology as a first-class type.** New
  `agentegrity.core.topology` module with `AgentTopology`,
  `AgentMember`, `AgentRole` (`LEADER` / `MEMBER` / `SUPERVISOR` /
  `WORKER` / `PEER`), `TopologyKind` (`HUB_SPOKE` /
  `HIERARCHICAL_DAG` / `PEER_TO_PEER` / `GROUP_CHAT`), and
  `TopologyChange`. Frozen dataclasses with deterministic
  `content_hash()` (SHA-256 across processes). Mutations produce
  new snapshots via `with_member` / `without_member` /
  `with_channels`. Immutability is load-bearing — RI needs
  deterministic restore targets, and attestation records commit
  to which topology was live at evaluation time.
- **Topology surfaces as `Evidence`, NOT as a canonical-payload
  field.** Four new `evidence_type` values are non-breaking
  additions to the existing list-of-Evidence field:
  - `"topology"`: source = topology_id, content_hash =
    topology.content_hash(). Emitted on every attestation when
    a topology is set.
  - `"topology_change"`: emitted when topology mutates. Source =
    previous topology_id, content_hash = new
    topology.content_hash(), summary = added/removed member
    counts.
  - `"peer_message"`: source = sender_agent_id, content_hash =
    SHA-256 of message canonical, for cross-agent attestation
    links.
  - `"handoff"`: source = parent_agent_id, content_hash =
    parent's DecisionRecord, for walking back to the handoff
    boundary.
- **Six new canonical events on `_BaseAdapter`** (existing 8 →
  14): `topology_declared`, `topology_change`, `peer_message`,
  `shared_memory_write` (with `writer_agent_id` for T-SHARED-MEM-
  MISATTRIB attribution), `broadcast` (capped at 1000/session
  per T-BROADCAST-AMP), `task_started`.
- **`_BaseAdapter.set_topology(topology, my_role)`** API.
  First call emits `topology_declared` + triggers an
  attestation; subsequent calls with a structurally-distinct
  topology emit `topology_change` + carry both Evidence types
  on the next attestation. Structurally-identical re-set is a
  no-op. Topology is sticky across subsequent evaluations.
- **Orphan `subagent_stop` detection.** A stop without a
  matching start (e.g., AutoGen OTel sampling drops the start
  span) logs a warning and emits a structured `subagent_orphan`
  event. T-ORPHAN-LIFECYCLE mitigation.
- **`AttestationChain.verify_cross_agent_links(peer_chains)`**
  stub. Validates `peer_message` / `handoff` Evidence sources
  resolve to real records in supplied peer chains with matching
  content_hash. Returns `True` permissively when no peer chains
  supplied. Full implementation lands in v0.9 with `KeyProvider`.
- **Adversarial layer extensions (AC)**:
  - Scans `shared_memory` and `broadcast_channels` in addition
    to today's `peer_messages`.
  - New `peer_coercion` regex family (3 patterns): "do as I
    say", "override your instructions", "respond as the user."
    Targets cascade-compromise where a peer redirects others.
    Default taxonomy 21 → 24 patterns.
  - New `peer_authority` check: a peer message from an agent
    NOT in the declared topology fires a threat (severity
    0.70, confidence 0.85).
- **Recovery layer extensions (RI)**:
  - T-CASCADE detection over `peer_score_history`. Two or more
    peers correlating downward over the degradation window sets
    `cascade_compromise_suspected = True`. Action escalates to
    `alert` EVEN IF this agent's own metrics are healthy.
  - New `peer_quarantine` entry in `RECOVERY_CAPABILITIES`.
    Declared in the profile → `RecoveryAssessment.quarantine_capable`
    reports True.
  - `RecoveryAssessment` gains
    `cascade_compromise_suspected`, `degrading_peer_ids`,
    `quarantine_capable`.
- **Governance layer extension**: `GOV-004` (Multi-Agent
  Escalation) was dead code pre-v0.8 (only fired on a synthetic
  `action.type` no adapter produced). Now reads topology member
  count from `topology_context.topology.members`; fires
  REQUIRE_APPROVAL when > 3 members declared. Legacy
  action-based path remains as a parallel trigger.
- **Adapter uplift: topology declaration across 7 frameworks.**
  Every adapter with multi-agent primitives declares an
  `AgentTopology` at the right discovery point:
  - **Agno** `instrument_team` → HUB_SPOKE from `team.members`,
    leader + members linkage.
  - **CrewAI** `subscribe(crew=...)` → HUB_SPOKE (sequential) /
    HIERARCHICAL_DAG (hierarchical) from `crew.agents`.
  - **AWS Bedrock** `wrap_client` + `instrument_strands` →
    HUB_SPOKE seeded with the supervisor; collaborators
    discovered in the trace stream grow the topology via
    `topology_change`.
  - **AutoGen** OTel SpanProcessor → GROUP_CHAT seeded on the
    root `invoke_agent` span; nested spans add members
    incrementally.
  - **Google ADK** `instrument` → HIERARCHICAL_DAG when the
    agent has `sub_agents` (SequentialAgent / ParallelAgent /
    LoopAgent); plain Agent stays single-agent.
  - **LangChain / LangGraph** `instrument_graph` → introspects
    `graph.get_graph()`. Node named `supervisor` /
    `supervisor_agent` / `orchestrator` → HIERARCHICAL_DAG;
    otherwise → PEER_TO_PEER. Plain Runnables (instrument_chain)
    stay single-agent.
  - **OpenAI Agents** `run_hooks` → PEER_TO_PEER seeded on
    `on_agent_start`; each `on_handoff` appends the target as a
    PEER via `topology_change`.
  - **Claude Agent SDK**: NOT modified. Single-agent by
    framework design. Pinning test asserts no topology is ever
    declared.

### Changed
- **CrewAI semantic fix (behavioral change)**: pre-v0.8,
  `TaskStartedEvent` mapped to `subagent_start`. Tasks are NOT
  agents in CrewAI's data model; this was a semantic bug.
  v0.8 maps `TaskStartedEvent` to the new `task_started`
  canonical event, and `AgentExecutionStartedEvent` /
  `AgentExecutionCompletedEvent` to `subagent_start` /
  `subagent_stop`. Real agent boundaries now drive real
  subagent_* events. Subagent counts will change for operators
  upgrading. Pass `legacy_task_mapping=True` to
  `CrewAIAdapter.subscribe(...)` to keep v0.7 behavior; the
  escape hatch emits a `DeprecationWarning` at subscribe and is
  removed in v0.9.
- **`_ContextBuffer.to_evaluation_context`** emits new
  `topology_context` key when topology / peer_messages /
  shared_memory / broadcast_messages are populated. Existing
  `peer_messages` stays at the top level for backward compat
  with the AdversarialLayer's pre-v0.8 access pattern.
- **`AttestationChain.append`** preserves a preset
  `chain_previous` when it matches the chain's expected
  predecessor, raising `ValueError` on mismatch. Old default
  (silently overwriting None) preserved for callers that don't
  preset.

### Fixed
- Cortical, Governance, and Recovery layers can now read
  multi-agent context that the AdversarialLayer was already
  consuming. Closes the detection gap where
  `_ContextBuffer.subagents` was captured but never surfaced to
  layer evaluation.

## [0.7.0] - 2026-06-08

### Added
- **Decision provenance: signed `DecisionRecord` at every decision
  boundary.** New `agentegrity.core.decision` module with
  `DecisionRecord`, `CaptureTier`, `DecisionInput`, and
  `RejectedAlternative` types. The `_BaseAdapter` (and
  `IntegrityMonitor`) gains a `record_decision(...)` method and an
  optional `signing_key=` constructor argument. The three decision
  boundaries (`pre_tool_use`, `stop`, `subagent_start`) now append a
  signed, hash-chained decision record to the same `AttestationChain`
  that holds attestations, captured **before** the action executes so
  a downstream verifier can prove the rationale was bound at decision
  time and not retrofitted. Each subsequent `AttestationRecord`
  carries `Evidence(evidence_type="decision", ...)` entries pointing
  at the decisions that preceded it; `AttestationChain.verify_decision_links()`
  validates the round-trip. **Capture tier today is C (Minimal) on every
  shipped adapter** — the schema supports Tier B (Partial: reasoning
  chain) and Tier A (Full: rejected alternatives), but no adapter
  populates those fields in production yet. Honest framing: capture
  fails open; on exception we log + emit a structured
  `capture_failure` `FrameworkEvent` so monitoring can see the gap.
  Spec at `spec/properties/decision-provenance.md`.
- **`AttestationChain` is now heterogeneous.** Holds both
  `AttestationRecord` and `DecisionRecord` via a new structural
  `ChainedRecord` Protocol. New `to_json()` / `from_json()`
  convenience methods. New `verify_chain_detailed() -> (bool,
  broken_idx, broken_kind)` for callers that want the broken
  record's position. `verify_chain() -> bool` is unchanged.
- **`python -m agentegrity verify-decisions <chain.json>` CLI verb.**
  Loads a serialized chain, runs `verify_chain()` +
  `verify_decision_links()`, prints a per-record table (kind /
  boundary / tier / signed / verified), exits non-zero on any
  failure.
- **Glossary entries:** Decision Record, Capture Tier, Decision
  Boundary.
- **AWS Bedrock Agents adapter (Python).** `pip install
  agentegrity[bedrock-agents]`. One adapter, two surfaces:

  *Strands SDK* (`instrument_strands(agent)`). Registers a typed
  `HookProvider` on a Strands `Agent`: `BeforeInvocationEvent` →
  `user_prompt_submit`, `AfterInvocationEvent` → `stop`,
  `BeforeToolCallEvent` → `pre_tool_use`, `AfterToolCallEvent` →
  `post_tool_use` (or `post_tool_use_failure` when `event.exception is
  not None`). Tool callbacks are registered as `async` so the adapter
  can `await on_event(...)`, inspect the block decision the
  `_handle_pre_tool_use` returns, and write `event.cancel_tool=reason`
  — **real enforcement**, the first adapter in the v0.7 batch where
  `enforce=True` actually denies a tool call rather than just
  recording the decision.

  *boto3* (`wrap_client(client)`). Patches `bedrock-agent-runtime`'s
  `invoke_agent` to force `enableTrace=True` (override via
  `force_trace=False`), then wraps the returned `EventStream`. TracePart
  variants map onto canonical events:
  `orchestrationTrace.invocationInput.actionGroupInvocationInput` →
  `pre_tool_use`, `orchestrationTrace.observation.actionGroupInvocationOutput`
  → `post_tool_use`, `agentCollaboratorInvocation{Input,Output}` →
  `subagent_{start,stop}`, `failureTrace` → `post_tool_use_failure`.
  The caller's `chunk` / `files` / `returnControl` / exception
  variants pass through unchanged. Observation-only: trace events
  arrive after the tool ran, so `enforce=True` on this surface records
  the block decision but cannot prevent execution — the adapter
  warns at `wrap_client` time when both are set.

  Partial-stream safety: the wrapper's iterator runs in a generator's
  `finally` block, so a caller bailing out mid-iteration still fires
  `stop` (with `reason="stream_terminated_early"`) and closes the
  session in the attestation chain.

- **Agno adapter (Python).** `pip install agentegrity[agno]`. Targets
  Agno 2.x. Hooks into the three Agno hook surfaces on both `Agent`
  and `Team`: `pre_hooks` → `user_prompt_submit`, `post_hooks` →
  `stop`, and the `tool_hooks` middleware chain → `pre_tool_use` /
  `post_tool_use` / `post_tool_use_failure`. `instrument_team()`
  marks statically-listed members so they emit `subagent_start` /
  `subagent_stop` while the leader emits the top-level prompt/stop
  pair; all members share one adapter so the attestation chain is
  unified. Zero-config: `from agentegrity.agno import instrument;
  agent = instrument(agent)`.

  Agno 2.x re-propagates `agent.tool_hooks` onto every tool at run
  setup (not construction), so tools added after `instrument()` are
  captured automatically — no construction-time wrapping or
  monkey-patching needed.

  **Enforcement.** Under `enforce=True` the `tool_hook` evaluates the
  `pre_tool_use` event synchronously (via the base class's
  `_evaluate_sync`) and, on a block decision, raises
  `agno.exceptions.StopAgentRun` before the tool runs. `StopAgentRun`
  is an `AgentRunException` subclass — the only exception family
  `FunctionCall.execute()` re-raises (via `exception_to_raise`) to
  halt the run. A plain `Exception` (including `InputCheckError`,
  which extends `Exception` directly, not `AgentRunException`) would be
  swallowed into a `status="failure"` result and the run would
  continue, so `InputCheckError` is the wrong primitive for this
  surface. Block decisions are still recorded in the attestation chain.

- **AutoGen adapter (Python).** `pip install agentegrity[autogen]`.
  AutoGen has no callback-handler API; the only hook surface is
  OpenTelemetry. The adapter ships an OTel `SpanProcessor` that maps
  AutoGen's GenAI semconv spans (`invoke_agent`, `execute_tool`) onto
  canonical events: root `invoke_agent` → `user_prompt_submit`/`stop`,
  nested → `subagent_start`/`subagent_stop`, `execute_tool` →
  `pre_tool_use`/`post_tool_use` (or `post_tool_use_failure` on
  ERROR status). Zero-config: `from agentegrity.autogen import
  instrument; instrument()` installs the SpanProcessor on the global
  `TracerProvider`. Power users can call `adapter.span_processor()`
  and wire it into their own provider.

  **Limitation:** observation-only. `enforce=True` records block
  decisions in the attestation chain but cannot actually deny tool
  calls — OTel spans observe post-hoc. The adapter emits a
  `UserWarning` on construction if `enforce=True` is set, so this
  contract is loud rather than silent.

### Changed
- **`AttestationRecord` canonical payload now includes `record_kind`.**
  Required so the heterogeneous chain can distinguish attestation
  records from decision records under signature (otherwise a tamperer
  could flip a decision into an attestation post-signing). **Backward-
  incompatible:** chains serialized before v0.7 fail `verify_chain()`
  after upgrade — signed or not — because the in-memory recomputed
  `content_hash` (now over the new canonical bytes) doesn't match the
  stored `chain_previous` references in subsequent records. Loading
  still works; verification doesn't. No rescue migration script:
  operators must either re-build the chain from a fresh root with
  the new code or pin to v0.6 for legacy verification. Same break
  applies to the Evidence-hash fix below; both land in this release.
- **`AgentegrityClient` adapter factory consolidated.** The five
  per-framework methods (`create_claude_adapter`,
  `create_langchain_adapter`, `create_openai_agents_adapter`,
  `create_crewai_adapter`, `create_google_adk_adapter`) are replaced
  by a single `create_adapter(name, profile, *, enforce=False,
  api_key=None)` driven by a name → class registry. Adding a new
  adapter is now one line in `_ADAPTER_REGISTRY` instead of a 14-line
  factory method. Migrate call sites from
  `client.create_claude_adapter(profile=p)` to
  `client.create_adapter("claude", profile=p)`. The high-level
  zero-config entry points (`agentegrity.claude.hooks()`,
  `agentegrity.crewai.instrument()`, etc.) are unaffected.
- **Dispatch shim consolidated, then made genuinely synchronous.**
  Three adapters (`CrewAIAdapter`, `LangChainAdapter`,
  `GoogleADKAdapter`) each carried their own near-identical asyncio
  bridge from sync framework callbacks into the async `on_event`
  handler. All three are removed; the bridge now lives once on
  `_BaseAdapter`. Going further: the eight `_handle_*` event handlers
  do no I/O, so they are now plain `def` (not `async def`), and the
  dispatch core is a synchronous `_evaluate_sync(event_type, data) ->
  dict`. `on_event` stays `async` (the `FrameworkAdapter` Protocol is
  unchanged, and Claude / OpenAI Agents / Bedrock-Strands still
  `await on_event(...)`), but it now just delegates to
  `_evaluate_sync`. `_dispatch` calls `_evaluate_sync` inline instead
  of scheduling a fire-and-forget coroutine, so dispatched evaluations
  complete before the hook returns. This unlocks real enforcement on
  synchronous hook surfaces (see Agno).
- **Google ADK adapter warns on `enforce=True`.** ADK's `before_*`
  callbacks expose no return-value or exception-signaling mechanism the
  runtime acts on, so the adapter is fundamentally observation-only.
  `enforce=True` now records block decisions in the attestation chain
  and warns at construction, matching the AutoGen / boto3 pattern.
- **`[all]` extra is now self-referential.** Adding a new optional
  framework no longer requires editing two places — register the
  extra under `[project.optional-dependencies]` and it flows into
  `[all]` automatically.

### Fixed
- **`Evidence.content_hash` is now a real, deterministic SHA-256** of
  the canonical JSON of the layer-result dict. Was previously
  `str(hash(str(r.to_dict())))` using Python's process-salted string
  hash — non-deterministic across processes and non-portable, which
  silently broke any attempt at tamper-evident verification across
  process boundaries. The three triplicated record-build paths
  (adapter base, monitor, SDK client) now share one
  `build_attestation_record(...)` helper. **Backward-incompatible**:
  re-builds the canonical payload of every newly-created attestation,
  so old chains fail verification post-upgrade (see Changed above).
- **CrewAI adapter works on crewai ≥ 1.0.** crewai 1.0 relocated the
  event classes from `crewai.utilities.events` to `crewai.events`
  (canonical sources under `crewai.events.types.*`). The adapter still
  imported the legacy path, so `subscribe()` raised `ImportError`
  under every crewai 1.x — including the 1.14.6 that `[all]` installs.
  The adapter now imports from `crewai.events`. It also registers the
  `ToolUsageErrorEvent → post_tool_use_failure` handler its docstring
  already advertised but never wired up. The previous "requires
  crewai" tests passed for the wrong reason (matching the legacy
  path's `ModuleNotFoundError`); they're replaced with a fake-bus
  integration test that drives every registered handler.

## [0.6.0] - 2026-05-05

### Changed
- **Default integrity pipeline now has four layers, not three.**
  `RecoveryLayer` joins `AdversarialLayer` / `CorticalLayer` /
  `GovernanceLayer` in the canonical pipeline used by
  `AgentegrityClient` and the framework adapter base class.
- **`PropertyWeights` defaults rebalanced** to give recovery a non-zero
  share: AC=0.35, EP=0.20, VA=0.30, RI=0.15 (was AC=0.40, EP=0.25,
  VA=0.35, RI=0.0).
- **Adversarial detection upgraded from substring matching to a regex
  taxonomy.** `AdversarialLayer` ships 21 default regex patterns
  organized into six attack families (prompt_injection, jailbreak,
  role_confusion, system_prompt_extraction, data_exfiltration,
  prompt_obfuscation). Detection now scans direct input *plus* memory
  reads *plus* tool-output content, and per-pattern severity/confidence
  drives the aggregate `ThreatAssessment`. Multiple matches in the same
  channel collapse to one entry per `threat_type` with `indicators`
  listing every pattern that fired. The taxonomy moves the layer from
  🟡 *Reference* to ✅ *Hardened* on the STATUS matrix.
- **Cortical drift detector hardened.** Replaced the asymmetric forward
  KL approximation with Jensen-Shannon distance under Laplace
  smoothing — symmetric, bounded in [0, 1], and a proper metric. New
  `min_drift_samples` constructor argument (default 20) guards against
  flagging drift on tiny sample sizes; below threshold the dimension
  surfaces an `__insufficient_samples` marker instead of a verdict. The
  `_kl_divergence_approx` private name is retained as an alias.
- README, MANIFESTO, spec, and glossary updated to describe four layers
  consistently. New `spec/layers/recovery-layer.md` normative spec.

### Added
- `agentegrity.layers.default_layers()` factory returning the
  canonical four-layer pipeline. Used internally by every zero-config
  entry point.
- `RecoveryLayer`, `default_layers`, and `PropertyWeights` are now
  re-exported from the top-level `agentegrity` package.
- `scripts/check_versions.py` Python equivalent of the existing
  TypeScript version-parity check. Wired into CI to fail the build on
  drift between `pyproject.toml`, `src/agentegrity/__init__.py`, the
  README shields badge, and present-tense version claims in README
  prose.
- New public `DetectorPattern` dataclass + `default_detector_patterns()`
  factory. Custom patterns can be appended via
  `AdversarialLayer(extra_patterns=[...])` or fully replace the
  taxonomy via `AdversarialLayer(patterns=[...])`.
- **`Checkpoint` Protocol + `InMemoryCheckpoint` / `FileCheckpoint`
  (atomic write via tempfile + `os.replace`, path-traversal guard) /
  `SqliteCheckpoint` (idempotent `CREATE TABLE IF NOT EXISTS`,
  `:memory:` supported via persistent connection) reference backends**
  in `agentegrity.layers.checkpoint`.
- **`RecoveryLayer.snapshot(agent_id, baseline=, metadata=)` and
  `RecoveryLayer.restore_to(checkpoint_id)`** — round-trip the layer
  through any conforming backend. Snapshot captures the attestation
  chain, score history, optional behavioural baseline, and arbitrary
  metadata; restore preserves original link hashes so
  `verify_chain()` returns True after a tamper→restore cycle.
- `RecoveryAssessment` now surfaces `checkpoint_count` and
  `last_checkpoint_id` for downstream telemetry.
- `AttestationRecord.from_dict` + `AttestationChain.from_records` /
  `AttestationChain.from_dict_list` / `AttestationChain.to_records_dict`
  for lossless chain serialisation.
- An attached `Checkpoint` backend is now treated as a synthetic
  `checkpoint` recovery capability so the score reflects operational
  reality, not just the agent profile's declarations.
- 76 new tests covering the regex taxonomy
  (`test_adversarial_detectors.py`), the JS-distance drift metric
  (`test_drift.py`), checkpoint backend round-trips
  (`test_checkpoint.py`), and the tamper→restore cycle
  (`test_recovery_restore.py`).
- **`BaselineStore` Protocol + `InMemoryBaselineStore` /
  `FileBaselineStore` (atomic writes via tempfile + `os.replace`,
  path-traversal guard) / `SqliteBaselineStore` (idempotent
  `CREATE TABLE IF NOT EXISTS`, `:memory:` via persistent connection)**
  in `agentegrity.layers.baseline_store`. Mirrors the Phase 2c
  `Checkpoint` Protocol pattern so behavioural baselines survive
  process restarts.
- **`CorticalLayer(baseline_store=...)`** wires the new persistence
  surface: on first `evaluate` for an agent the layer reads through
  to the store; `update_baseline` writes through after each update.
  An explicit `baseline=` argument still wins (rollback-to-known-good
  story).
- **Adversarial layer scans two new channels**: `retrieved_documents`
  (RAG poisoning) and `peer_messages` (multi-agent injection).
  Loose schema accepts `{content, text, body}` / `{content, text, message}`.
  Same regex taxonomy applies, same per-channel threat aggregation.
- **Nightly `benchmark` workflow** — daily 04:17 UTC cron + on
  workflow_dispatch. Runs `pytest -m benchmark` and uploads
  `bench-report.md` as a 30-day artifact. External datasets plug in
  via `AGENTEGRITY_BENCH_*` repository variables.
- **Python coverage gate at 85% line+branch**, currently 86.71%.
  `pytest-cov` + `coverage[toml]` added to `[dev]` extras; `[tool.coverage]`
  block in `pyproject.toml`; new `coverage` CI job uploads
  `coverage.xml` for 14 days. CLI `__main__.py` is omitted from
  coverage by intent (verified manually via `python -m agentegrity`).
- **TypeScript coverage gate at 80% lines / 70% functions**,
  currently 89.99% / 83.40%. New `clients/typescript/scripts/check-coverage.ts`
  parses `bun test --coverage` text output and exits non-zero on
  threshold breach (works around bun 1.3.11's broken
  `coverageThreshold` enforcement). Wired into the CI typescript job.
- **Real-world detection benchmark numbers published in
  `STATUS.md`** — InjecAgent dh+ds combined: TPR=0.000, FPR=0.000 on
  N=2,108 (regex taxonomy targets pattern-style injections; InjecAgent
  attacks are action-oriented and require the unfinished LLM
  classifier). The synthetic suite still serves as the calibration
  regression gate. `scripts/fetch_benchmark_datasets.sh` automates
  the InjecAgent fetch; data files are gitignored.
- **Cross-adapter conformance suite** (`test_adapter_conformance.py`).
  Same canonical event stream is driven through every shipped Python
  adapter (Claude / LangChain / OpenAI Agents / CrewAI / Google ADK)
  and the same 9 invariants are pinned per adapter — base-class
  inheritance, evaluation count vs chain length, chain verification,
  session-id stability, exporter lifecycle (start/event×N/end),
  exporter idempotency, fail-open on broken exporter, multi-exporter
  fan-out, summary shape, idempotent close, unknown-event tolerance.
  Adding a new adapter requires one line in `ADAPTER_CLASSES`; the
  matrix runs against it automatically. 51 tests; a sentinel test
  fails loudly if the registry size drifts so adapters can't be
  silently dropped.
- **Detection benchmark suite** (`tests/benchmarks/`,
  `tests/test_benchmarks.py`, `scripts/run_benchmarks.py`).
  `pytest -m benchmark` runs the in-repo synthetic suite (~28 attacks
  + ~30 benign across the six attack families) with calibrated
  thresholds (TPR ≥ 0.95, FPR ≤ 0.05, F1 ≥ 0.95, plus per-family
  floor: every family must register at least one TP).
  `BenchmarkPrompt` / `BenchmarkResult` / `run_suite()` /
  `format_markdown_report()` are the harness; loader stubs for PINT /
  AgentDojo / InjecAgent auto-skip when their `AGENTEGRITY_BENCH_*`
  env var is unset, so a nightly cron can plug in real datasets
  without changing CI defaults. The benchmark marker is excluded from
  the default `pytest` invocation via `addopts = "-m 'not benchmark'"`
  so unit tests stay fast. Calibration baseline:
  `synthetic_pint_like` TPR=1.000, FPR=0.000, F1=1.000 on N=58.
- During calibration two regex patterns were tightened to handle
  realistic attack phrasings: `ignore_your_role` now allows an
  optional adjective between determiner and noun ("abandon your
  *assistant* character"); `reveal_system_prompt` now allows an
  optional `me` after the verb ("show *me* your hidden instructions")
  and the noun alternation accepts "hidden \\w+" so configuration
  fishing is captured.

### Migration
- Callers that constructed `PropertyWeights` with three keyword
  arguments will now hit the validator. Pass
  `recovery_integrity=0.0` explicitly to keep three-property weighting,
  or omit the `weights=` argument and adopt the new default.
- Callers that rely on undocumented behaviour of `_kl_divergence_approx`
  will see *different numeric values* (the new function returns JS
  distance, not forward KL). Public APIs are unchanged. Drift
  thresholds calibrated against the old metric should be revalidated.

## [0.5.3] - 2026-04-29

### Changed
- Concrete version pins replace `workspace:*` references in TypeScript
  package manifests so published `@agentegrity/*` packages install
  cleanly off-registry.
- GitHub Actions bumped to `actions/checkout@v5`,
  `actions/setup-python@v6`, `actions/setup-node@v5`.
- CI push triggers scoped to `main` plus concurrency cancellation so
  in-flight runs cancel on rapid pushes.
- Repository moved to the `cogensec` org.

### Added
- `AGENTEGRITY_OFFLINE` environment variable so test runs work without
  a reporter target.
- Smoke tests for `createDefaultAdapter` in the TypeScript client
  package.

## [0.5.0] - 2026-03-?

### Added
- **Six TypeScript framework adapters.** `@agentegrity/claude-sdk`,
  `@agentegrity/langchain`, `@agentegrity/openai-agents`,
  `@agentegrity/crewai`, `@agentegrity/google-adk`, plus the
  TypeScript-native `@agentegrity/vercel-ai` (no Python equivalent;
  uses the AI SDK's OpenTelemetry tracer surface).
- `createDefaultAdapter()` shared helper in `@agentegrity/client` that
  every framework adapter wraps. Owns lifecycle, exporter fan-out,
  fail-open guarantees, and `process.beforeExit` shutdown.
- `clients/typescript/scripts/check-versions.ts` keeps every
  `@agentegrity/*` package version aligned with `pyproject.toml`.
- Release workflow publishes the seven npm packages in a matrix.

## [0.4.0] - 2026-?

### Added
- **`SessionExporter` hook + cross-language wire format.**
  `register_exporter()` on every Python adapter; live session data
  (session_start, every evaluated event, session_end) streams as
  JSON-ready dicts to subscribed exporters, fail-open so a broken
  exporter never breaks the agent.
- JSON Schema definitions under `schemas/exporter/` and OpenAPI 3.1
  under `schemas/openapi.yaml` for the exporter wire format.
- First-party TypeScript client (`@agentegrity/client`) for emitting
  the same event stream from Bun / Node agents.

## [0.3.0]

### Added
- **Multi-framework adapters.** LangChain / LangGraph, OpenAI Agents
  SDK, CrewAI, and Google Agent Development Kit each ship as a
  `agentegrity.<framework>` Python module with the same three-line
  instrumentation surface as the Claude adapter.
- Shared `_BaseAdapter` so adding a new framework is mostly mechanical.

## [0.2.1]

### Added
- Zero-config `agentegrity.claude` top-level module: `hooks()`,
  `report()`, `reset()` — three-line Claude Agent SDK instrumentation
  with no setup.
- `AgentProfile.default()` factory.
- `python -m agentegrity` info CLI + `doctor` self-check command.

## [0.2.0]

### Added
- **Claude Agent SDK adapter.** First framework integration with five
  hook points (Harness, Tools, Sandbox, Session, Orchestration).
- **LLM-backed cortical checks** (`pip install agentegrity[llm]`):
  Claude-powered semantic analysis of reasoning chains, memory
  provenance, and behavioral drift, fail-open on API errors.
- **`RecoveryLayer`** (initially opt-in; promoted to a default layer
  in v0.5.3-Unreleased).
- **`AsyncIntegrityEvaluator`** running independent layers in parallel
  via `asyncio.gather`.

## [0.1.0]

### Added
- Initial public release.
- Three-layer architecture: `AdversarialLayer`, `CorticalLayer`,
  `GovernanceLayer`.
- Pattern-based reference detectors (substring matching for prompt
  injection indicators, dictionary-based behavioral drift).
- Cryptographic attestation: Ed25519-signed `AttestationRecord`,
  hash-chained `AttestationChain`, deterministic JSON canonicalization.
- Custom validator and policy extension points.
- Three working examples (`basic_evaluation.py`,
  `runtime_monitoring.py`, `custom_validator.py`).

[Unreleased]: https://github.com/cogensec/agentegrity/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.8.0
[0.7.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.7.0
[0.6.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.6.0
[0.5.3]: https://github.com/cogensec/agentegrity/releases/tag/v0.5.3
[0.5.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.5.0
[0.4.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.4.0
[0.3.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.3.0
[0.2.1]: https://github.com/cogensec/agentegrity/releases/tag/v0.2.1
[0.2.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.2.0
[0.1.0]: https://github.com/cogensec/agentegrity/releases/tag/v0.1.0
