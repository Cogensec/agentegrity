# Project Status

A scannable matrix of where each piece of the framework is on the
maturity curve. This is the answer to "what's actually production-ready
versus reference-quality versus experimental."

The README's *What it does and does not* prose explains the philosophy;
this document is the operational version of it.

**Legend**

- ✅ **Hardened** — production-grade, tested against adversarial inputs,
  cryptographically grounded, or otherwise carrying load. Safe default.
- 🟡 **Reference** — real working logic with end-to-end test coverage,
  but the detection / heuristic content is the published reference set.
  Catches obvious cases; production deployments should extend with
  custom detectors / rules / providers.
- 🧪 **Experimental** — feature exists, has at least smoke tests, but
  the API or behaviour may change before v1.0.
- 🛠 **Planned** — on the roadmap, not yet shipped. Documented for
  transparency.

---

## Core (`src/agentegrity/core/`)

| Module                                     | Status | Notes |
|--------------------------------------------|:------:|-------|
| `evaluator.IntegrityEvaluator`             |   ✅   | Sync four-layer pipeline; composite scoring with configurable `PropertyWeights`; fail-fast on `block`. |
| `evaluator.AsyncIntegrityEvaluator`        |   ✅   | Runs independent layers via `asyncio.gather` when `fail_fast=False`. Wraps sync layers via `asyncio.to_thread`. |
| `attestation.AttestationRecord`            |   ✅   | Ed25519 signing via `cryptography`, deterministic JSON canonicalization, SHA-256 content hash. |
| `attestation.AttestationChain`             |   ✅   | Hash-chained tamper-evident history; `verify_chain()` covers all linked records. |
| `monitor.IntegrityMonitor`                 |   ✅   | `@guard` decorator, violation callbacks, four `ViolationAction` modes. |
| `profile.AgentProfile`                     |   ✅   | Type-safe enums for `AgentType` / `DeploymentContext` / `RiskTier`; `default()` factory. |

## Layers (`src/agentegrity/layers/`)

| Layer              | Default? | Status | Detection Quality |
|--------------------|:--------:|:------:|-------------------|
| `AdversarialLayer` |   ✅     |   ✅   | Regex-pattern taxonomy across six families (prompt_injection, jailbreak, role_confusion, system_prompt_extraction, data_exfiltration, prompt_obfuscation). 21 default patterns scan direct input + memory reads + tool outputs; per-pattern severity/confidence; aggregation collapses multiple matches per (channel, threat_type). Custom patterns plug in via `extra_patterns=`. Embedding-similarity + LLM-backed semantic classifier still on the post-0.5.x roadmap. |
| `CorticalLayer`    |   ✅     |   ✅   | Reasoning conflict detection still rule-based (🟡). Memory provenance still structural (🟡). **Drift: Jensen-Shannon distance with Laplace smoothing**, symmetric and bounded, with a `min_drift_samples` guard so small distributions don't produce noisy verdicts. The legacy `_kl_divergence_approx` is retained as an alias. Semantic reasoning checks via `cortical_llm` are opt-in. |
| `CorticalLLMLayer` (`cortical_llm.py`) | opt-in | 🧪 | Anthropic-API-backed semantic checks; fail-open on API error. Requires `pip install agentegrity[llm]`. |
| `GovernanceLayer`  |   ✅     |   ✅   | Real policy engine, `enterprise-default` rule set, custom rule support, audit log with SHA-256 content hash. |
| `RecoveryLayer`    |   ✅     |   ✅   | Capability declaration check, sustained-degradation detection on score history, attestation-chain continuity, **and a real `Checkpoint` Protocol with `InMemoryCheckpoint` / `FileCheckpoint` (atomic write) / `SqliteCheckpoint` (idempotent schema) reference backends**. `RecoveryLayer.snapshot()` persists chain + score history + baseline + metadata; `restore_to(checkpoint_id)` rebuilds the chain (preserving original link hashes so `verify_chain()` returns True post-restore). External backends (S3, Redis, KMS-wrapped) are pluggable by satisfying the four-method Protocol. |

## Python Adapters (`src/agentegrity/<framework>.py`)

| Adapter                           | Status | Notes |
|-----------------------------------|:------:|-------|
| `agentegrity.claude` (Claude Agent SDK) | ✅ | Five hook points: Harness, Tools, Sandbox, Session, Orchestration. |
| `agentegrity.langchain` (LangChain + LangGraph) | ✅ | Single adapter covers both via callback-handler propagation. |
| `agentegrity.openai_agents` | ✅ | Hooks via the official `openai-agents` Python SDK. |
| `agentegrity.crewai` | ✅ | Event-bus subscription. |
| `agentegrity.google_adk` | ✅ | Google Agent Development Kit. |

All five inherit from `_BaseAdapter` (`adapters/base.py`), share the
seven canonical event types, and feed the same evaluator + attestation
chain.

## TypeScript Packages (`clients/typescript/packages/`)

| Package                    | Status | Notes |
|----------------------------|:------:|-------|
| `@agentegrity/client`      |   ✅   | Shared `createDefaultAdapter()`, `AgentegrityReporter`, types, `process.beforeExit` shutdown, exporter fan-out. |
| `@agentegrity/claude-sdk`  |   ✅   | Mirrors the Python Claude adapter. |
| `@agentegrity/langchain`   |   ✅   | LangChain JS callback handler. |
| `@agentegrity/openai-agents` | ✅  | OpenAI Agents JS SDK. |
| `@agentegrity/crewai`      |   ✅   | CrewAI JS event hooks. |
| `@agentegrity/google-adk`  |   ✅   | Google ADK JS bindings. |
| `@agentegrity/vercel-ai`   |   🧪   | TS-native; uses the AI SDK's OpenTelemetry tracer surface. No Python equivalent. |

## Spec & Schemas

| Asset                                | Status | Notes |
|--------------------------------------|:------:|-------|
| `spec/SPECIFICATION.md`              |   🟡   | Currently labeled `v1.0-draft`. Phase 6 plan: lock to v1.0. |
| `spec/layers/adversarial-layer.md`   |   ✅   | Normative. |
| `spec/layers/cortical-layer.md`      |   ✅   | Normative. |
| `spec/layers/governance-layer.md`    |   ✅   | Normative. |
| `spec/layers/recovery-layer.md`      |   🧪   | Newly added in v0.5.3-Unreleased; conformance section subject to revision. |
| `spec/properties/*.md`               |   ✅   | Per-property normative docs (AC / EP / VA). |
| `schemas/exporter/*.json`            |   ✅   | JSON Schema for `event`, `session_start`, `session_end`, `common`. |
| `schemas/openapi.yaml`               |   ✅   | OpenAPI 3.1 description of the exporter wire format. |

## Operations & Tooling

| Capability                           | Status | Notes |
|--------------------------------------|:------:|-------|
| Lint (`ruff`)                        |   ✅   | Clean. |
| Type check (`mypy --strict`)         |   ✅   | 27 source files, zero issues. |
| Python tests                         |   ✅   | 147 tests, all green. |
| TypeScript build / typecheck / test  |   ✅   | All 7 packages green via `bun run`. |
| CI matrix (Python 3.10/3.12, Node 18/20/22) | ✅ | `.github/workflows/ci.yml`. |
| Version-parity gate                  |   ✅   | `scripts/check_versions.py` (Python) + `scripts/check-versions.ts` (TS) wired into CI. |
| Release workflow                     |   ✅   | `.github/workflows/release.yml` publishes Python wheel + npm matrix. |
| Conformance test suite (cross-adapter) | ✅ | `tests/test_adapter_conformance.py` runs the same canonical event stream + lifecycle assertions across every shipped adapter (51 tests today; matrix of 9 invariants × 5 adapters + a registry-stability sentinel). New adapters add one line to `ADAPTER_CLASSES` and inherit the entire matrix. |
| Detection benchmark suite            |   ✅   | `pytest -m benchmark` runs the in-repo synthetic suite (~30 attacks + ~30 benign across 6 attack families) with calibrated thresholds (TPR ≥ 0.95, FPR ≤ 0.05, F1 ≥ 0.95, plus per-family floor: every family must register at least one TP). Loader stubs for PINT / AgentDojo / InjecAgent auto-skip when their `AGENTEGRITY_BENCH_*` env var is unset, so cron can plug in real datasets without touching CI defaults. `scripts/run_benchmarks.py [--all]` prints a markdown report and exits non-zero on regression. Headline calibration: synthetic TPR=1.000, FPR=0.000, F1=1.000 on N=58. |
| OpenTelemetry instrumentation        |   🛠   | Phase 5 plan. |
| Prometheus metrics                   |   🛠   | Phase 5 plan. |
| SLSA provenance + SBOM + sigstore    |   🛠   | Phase 6 plan; provenance was disabled while repo was private and is now eligible to re-enable. |

## Roadmap Items Not Yet Shipped

| Item                                  | Phase | Notes |
|---------------------------------------|:-----:|-------|
| Semantic Kernel adapter (Python + TS) |   4   | `pip install agentegrity[semantic-kernel]` + `@agentegrity/semantic-kernel`. |
| AutoGen adapter (Python)              |   4   | `pip install agentegrity[autogen]`. |
| AWS Bedrock Agents adapter (Python)   |   4   | `pip install agentegrity[bedrock]`. |
| Reference SessionExporter receiver    |   4   | Self-hostable FastAPI app under `examples/exporter_receiver/`. |
| JWS / COSE attestation serializations |   6   | Interop with generic verifiers; raw Ed25519 stays the default. |
| Key rotation + KMS interface          |   6   | `KeyProvider` Protocol with file / env / AWS KMS impls. |
| Threat model document                 |   6   | `spec/threat-model.md`. |
| Reference docs site (MkDocs / Docusaurus) | 7 | Auto-generated API reference + per-layer / per-adapter guides. |

Phases reference the canonical dev plan; see `/root/.claude/plans/`
during active development or the `docs/dev-plan.md` once it lands in
the repo.

---

**Last reviewed:** v0.5.3 + Phase 2a/2b/2c/2d/2e (2026-05-04). This
file is the source of truth for "what's done." Update it in the same
commit that ships a status change.
