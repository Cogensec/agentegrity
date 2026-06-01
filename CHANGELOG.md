# Changelog

All notable changes to the Agentegrity Framework are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0 minor versions may contain breaking changes; the project remains
in beta until the v1.0 stability criteria documented in
[README → Roadmap](README.md#roadmap) are met.

## [Unreleased]

### Added
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
- **Sync→async dispatch shim lifted to `_BaseAdapter`.** Three
  adapters (`CrewAIAdapter`, `LangChainAdapter`, `GoogleADKAdapter`)
  each carried their own near-identical asyncio bridge from sync
  framework callbacks into the async `on_event` handler. All three
  are removed; the bridge now lives once on `_BaseAdapter` as
  `_dispatch(event_type, data)`. Google ADK's
  `_dispatch_sync` rename folds in here too — call sites use
  `_dispatch` regardless of framework.
- **`[all]` extra is now self-referential.** Adding a new optional
  framework no longer requires editing two places — register the
  extra under `[project.optional-dependencies]` and it flows into
  `[all]` automatically.

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

[Unreleased]: https://github.com/cogensec/agentegrity-framework/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.6.0
[0.5.3]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.5.3
[0.5.0]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.5.0
[0.4.0]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.4.0
[0.3.0]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.3.0
[0.2.1]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.2.1
[0.2.0]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.2.0
[0.1.0]: https://github.com/cogensec/agentegrity-framework/releases/tag/v0.1.0
