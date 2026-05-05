# Agentegrity Framework — Threat Model

**Status:** Normative
**Version:** 0.6.0
**Last reviewed:** 2026-05-06

---

This document is the [STRIDE](https://en.wikipedia.org/wiki/STRIDE_model)
threat model for the framework itself. Required reading for anyone
deploying agentegrity in production or putting it in front of a
security review board. Companion to [`SECURITY.md`](../SECURITY.md)
(disclosure policy) and [`STATUS.md`](../STATUS.md) (per-component
maturity).

The questions this document answers:

- What can an attacker do to the framework or its outputs?
- Which mitigations exist today, and where in the code they live.
- Which mitigations are explicitly *not* present and why.
- Which residual risk we accept and which we ask the operator to handle.

> The framework is a *measurement* layer. It does not replace
> guardrails, runtime monitors, or network controls. Anything below
> that talks about external mitigations assumes those exist —
> agentegrity is defense in depth, not defense in replacement.

---

## 1. System under analysis

For this threat model the framework is everything in this repository
plus the published `agentegrity` PyPI package and `@agentegrity/*` npm
scope. External systems (the agent's underlying LLM, the framework SDK
it's instrumenting, the operator's exporter backend) are trust
boundaries — we model what they can do *to* agentegrity and what
agentegrity does to protect itself across those boundaries.

### 1.1 Trust boundaries

```
┌─────────────────────────────────────────────────────────────┐
│ User process (Python or Node)                               │
│                                                             │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │ Framework  │←─→│ AgentegrityX │←─→│ AttestationChain │   │
│  │ SDK        │   │ Adapter      │   │ + signing key    │   │
│  └────────────┘   └─────┬────────┘   └──────────────────┘   │
│        ▲                │                  │                │
│        │                ▼                  ▼                │
│        │          ┌──────────┐       ┌──────────┐           │
│        │          │ Layers   │       │ File /   │           │
│        │          │ (4)      │       │ sqlite   │           │
│        │          └──────────┘       │ checkpts │           │
│        │                              └──────────┘          │
│        ▼                                                    │
│  ┌────────────┐                                             │
│  │ LLM        │←══ network                                  │
│  └────────────┘                                             │
└─────────────────────────────────────────────────────────────┘
                  │ (HTTP, fan-out)
                  ▼
        ┌───────────────────────┐
        │ SessionExporter       │
        │ backend (operator)    │
        └───────────────────────┘
```

Trust boundaries (each of these is an attack surface):

| # | Boundary | Description |
|---|---|---|
| TB-1 | Framework SDK ↔ Adapter | The framework SDK can pass arbitrary content into adapter event handlers. |
| TB-2 | LLM ↔ Adapter | LLM output (tool responses, retrieved documents, peer messages) reaches the AdversarialLayer's scanned channels. |
| TB-3 | Adapter ↔ Layers | The four-layer evaluator runs in-process; trusted. |
| TB-4 | Layers ↔ Persistence | Checkpoint and BaselineStore writes go to local disk or sqlite. |
| TB-5 | Adapter ↔ Exporter | Session data (start, events, end) is fanned out to caller-registered exporters and the HTTP reporter. |
| TB-6 | User process ↔ Operator backend | The exporter backend (e.g. `agentegrity-pro`, the reference receiver, a custom backend) sees the wire format. |
| TB-7 | Maintainers ↔ Public registries | PyPI and npm releases are signed by the GitHub release workflow's OIDC identity (in 0.6.0+ when SLSA provenance is re-enabled). |

---

## 2. STRIDE

For each STRIDE category, we list the threats relevant to this
framework, the mitigation in place, and the residual risk.

### 2.1 Spoofing

**T-S1 — Forged AttestationRecord with attacker-chosen content.**
An attacker who can sign records with the framework's private key
can produce records that pass `AttestationRecord.verify()`.

- **Mitigation.** The signing key is supplied by the caller — the
  framework never generates or stores a key on the user's behalf.
  `AttestationRecord.sign(private_key)` writes the signature and
  embedded public key to the record. Verification is via embedded
  public key OR an explicit public_key argument so an operator can
  pin to a known good key.
- **Operator must.** Generate a fresh Ed25519 key per agent (or per
  agent-tier), store it in a KMS or HSM, and never pass key material
  through the same trust boundary as agent input. v0.7 will ship a
  `KeyProvider` Protocol so this is enforced architecturally rather
  than only by convention.
- **Residual risk.** If the operator stores the signing key in the
  same process as the LLM that the agent talks to and the LLM
  achieves arbitrary code execution (for example via a tool that
  exec's a subprocess on attacker-controlled input), the key is
  reachable. Use OS-level isolation or a KMS for any deployment
  where this matters.

**T-S2 — Imposter SessionExporter.**
A malicious package on PyPI/npm could register itself as a
`SessionExporter` and receive every event flowing through the agent.

- **Mitigation.** Exporter registration is explicit — `register_exporter(...)`
  must be called by the user's code. There is no auto-discovery
  pathway. Supply-chain policy (lockfiles, allowlists) is the
  operator's responsibility.
- **Operator must.** Pin every dependency; review what
  `register_exporter` calls live in your codebase.

**T-S3 — Imposter HTTP exporter backend.**
The agent points `AGENTEGRITY_URL` at a backend; an attacker
controlling DNS or the network path can serve a different one.

- **Mitigation.** TLS is the operator's responsibility. The reporter
  uses `fetch` (TS) / `httpx` (Python) which honour standard CA
  bundles. There is no certificate pinning today.
- **Residual risk.** A network-level attacker between the agent and
  the backend can observe (and potentially modify) the wire payload.
  Use mTLS or a private network for production deployments.

### 2.2 Tampering

**T-T1 — Tampered AttestationRecord on disk or in transit.**

- **Mitigation.** Each record is Ed25519-signed; `verify()` returns
  False on any byte change. The chain links every record to the
  SHA-256 of its predecessor's canonical payload, so a single
  tampered record breaks the entire downstream chain via
  `AttestationChain.verify_chain()`.
- **Cross-link tested in.** `tests/test_recovery_restore.py`,
  `tests/test_attestation*.py`.
- **Residual risk.** The verifier needs the legitimate public key. An
  attacker who can swap both the record AND the embedded public key
  produces a chain that verifies internally — operators MUST verify
  against a known-good key, not against the embedded one.

**T-T2 — Tampered Checkpoint or BaselineStore file.**
The reference file backends write JSON to disk. An attacker with
filesystem write access can mutate baselines or checkpoints.

- **Mitigation.** Atomic writes (`NamedTemporaryFile` + `os.replace`)
  prevent half-written files. JSON contents are NOT signed today.
- **Operator must.** Restrict filesystem permissions. Treat the
  checkpoint directory as a secret-equivalent — anyone who can write
  there can roll the agent back to a state of their choosing.
- **Residual risk.** Accepted. v0.7+ will add optional signature
  envelope around persisted artifacts via the `KeyProvider` interface.

**T-T3 — Policy rule tampering at rest.**
`GovernanceLayer` reads policy rules from the policy_set name +
custom rules at construction time. If the rule source (a config file,
a database row) is mutable by an attacker, the agent's authorization
behavior changes.

- **Mitigation.** Rules are passed as code, not loaded from disk by
  default. The `policy_set="enterprise-default"` is a constant
  defined in `src/agentegrity/layers/governance.py`.
- **Operator must.** If you load custom rules from a file, sign them
  separately and verify before passing them to `GovernanceLayer`.
  Audit log entries are SHA-256-hashed so post-hoc detection of
  rule changes is possible — but only if you compare against a
  trusted hash record.

**T-T4 — Tampering with the regex detector taxonomy at runtime.**
A malicious dependency could monkey-patch
`agentegrity.layers.adversarial.default_detector_patterns()` to
return a weakened set.

- **Mitigation.** None at runtime. Python and JS both allow
  monkey-patching arbitrarily.
- **Operator must.** Pin every dependency and use `pip-audit`/`npm
  audit` in CI. Consider running with `agentegrity` in an isolated
  process with restricted module imports.

### 2.3 Repudiation

**T-R1 — Operator denies an evaluation occurred.**

- **Mitigation.** Every evaluation produces an `AttestationRecord`
  with a UUID, a signed timestamp, and a chain link to the previous
  record. The chain is independently verifiable by any third party
  with the public key — the framework does not need to be trusted to
  prove what it observed.
- **Operator must.** Persist the chain. The library does not write
  to durable storage on its own.

**T-R2 — Agent denies it received an injection.**

- **Mitigation.** The AdversarialLayer's `ThreatAssessment` becomes
  part of the `AttestationRecord.layer_states`. A signed record with
  `threat_count > 0` is permanent evidence that the agent's
  evaluation pipeline saw the attack.
- **Residual risk.** If the layer didn't catch the attack (see
  STATUS.md detection-quality discussion — the regex taxonomy misses
  action-oriented injections), the absence of a threat in the
  record only proves the layer didn't fire, not that no attack
  occurred. Detection quality is the operator's responsibility to
  validate against their own threat model.

### 2.4 Information disclosure

**T-I1 — Sensitive context leaks via SessionExporter fan-out.**
Every adapter event is fanned out to every registered exporter. If
the agent context contains secrets (API keys, PII in tool responses,
private documents in retrieved_documents), they reach every
subscriber.

- **Mitigation.** None at the framework layer. The exporter wire
  format is not sanitised.
- **Operator must.** Scrub sensitive fields before they reach the
  framework, use a custom validator to redact specific fields, or
  put a sanitising proxy in front of the exporter receiver. The
  reference receiver in `examples/exporter_receiver/` deliberately
  prints the full payload so operators see what their exporters
  receive.

**T-I2 — Signing key disclosure via debug logging.**
Any operator code path that `repr()`s an `AttestationRecord` could
inadvertently expose the signature bytes (low risk) or the public
key (informational, not a secret).

- **Mitigation.** `AttestationRecord.__repr__` redacts the signature
  to a truncated hex prefix; the full signature is only visible via
  `to_dict()` which the user explicitly invokes.
- **Operator must.** Avoid logging private keys. The framework never
  receives the private key in a form it would log — only the
  public key is embedded in the record.

**T-I3 — Cross-tenant leakage in checkpoint stores.**
Multiple agents sharing a single `FileBaselineStore` directory or
`SqliteBaselineStore` database see each other's baselines.

- **Mitigation.** The store is keyed by `agent_id`, but there is no
  ACL — every agent with a handle to the store can read every other
  agent's state.
- **Operator must.** Isolate stores per-tenant. The backends are
  cheap to instantiate; create one per tenant rather than one per
  cluster.

### 2.5 Denial of service

**T-D1 — Resource exhaustion via huge prompts.**
An attacker controls the agent's input. Each AdversarialLayer
evaluation runs every regex pattern against the input text.

- **Mitigation.** Regex patterns are anchored on word boundaries and
  use bounded character classes — none use unbounded backreferences
  that would enable ReDoS. The default 21-pattern taxonomy runs in
  ~0.2 ms p95 on representative input (see `tests/test_perf_budget.py`).
- **Residual risk.** The framework does not impose a maximum input
  length. An attacker passing a 10 MB string will pay 10 MB worth of
  regex CPU. Use the operator's input-size limit at the framework
  SDK level.

**T-D2 — Exporter starvation.**
A slow exporter (e.g. a webhook to a backend that's down) could in
principle stall the adapter.

- **Mitigation.** Exporter callbacks are awaited via `_safe_await` /
  `safeCall` with a `try/except` that catches and logs every
  exception. Slow exporters block the await but cannot crash the
  agent. There is no per-exporter timeout in 0.6.0.
- **Operator must.** Use a non-blocking HTTP client or a fire-and-
  forget queue if the exporter target is unreliable. v0.7+ will add a
  per-exporter timeout knob.

**T-D3 — Chain-verification cost on long-running agents.**
`AttestationChain.verify_chain()` is O(n) over chain length. An
agent running for weeks with millions of records pays linear cost
on each verify.

- **Mitigation.** Verification is opt-in. Operators choose when to
  verify; the chain doesn't auto-verify on append.
- **Operator must.** Roll over to a fresh chain at deployment
  boundaries. The Checkpoint Protocol gives a clean cut-point.

### 2.6 Elevation of privilege

**T-E1 — Path traversal in FileCheckpoint / FileBaselineStore.**
A malicious or buggy caller passes an `agent_id` like
`../../etc/passwd` — without a guard the store could write outside
its root.

- **Mitigation.** `FileCheckpoint._path_for` and
  `FileBaselineStore._path_for` raise `ValueError` on any id
  containing `/`, `\\`, or `..`. Tested in `tests/test_checkpoint.py`
  and `tests/test_baseline_store.py`.
- **Residual risk.** Accepted as low — would require an attacker to
  control `agent_id` and the filesystem behaviour. Both backends
  use pure-Python path checks; no shell interpolation.

**T-E2 — Arbitrary code execution via custom validator.**
The framework lets operators register custom threat detectors and
custom policy rules that are arbitrary Python callables. A malicious
dependency that adds a custom detector escalates to full process
control.

- **Mitigation.** None. Custom callables are explicitly an extension
  point and their code runs in the agent's process.
- **Operator must.** Treat custom detector / policy registration as
  a trust-decision; don't accept callables from untrusted sources.
  Code review every custom hook.

**T-E3 — Supply-chain attack on `agentegrity` itself.**
A compromised maintainer, stolen npm token, or compromised CI
credential could publish a malicious release.

- **Mitigation today.** PyPI publishing uses GitHub Actions OIDC
  trusted publishing — there are no long-lived API tokens in CI.
  Two-factor authentication is enforced on all maintainer accounts.
- **Mitigation v0.7+** SLSA provenance generation in `release.yml`
  (currently disabled — the bump went out without it after the repo
  moved to public/cogensec; will be re-enabled in v0.7), SBOM
  attached to every GitHub release, sigstore signature on release
  artifacts.
- **Operator must.** Pin exact versions, verify SLSA provenance once
  it's re-enabled, and use `pip install agentegrity==0.6.0
  --require-hashes` against a lock file.

---

## 3. Out of scope

The threat model deliberately does not cover:

- **The agent's own LLM provider.** Agentegrity has no opinion on
  whether the LLM is hosted by Anthropic, OpenAI, on-prem, etc. Trust
  the LLM at whatever level your existing vendor-risk process trusts
  it.
- **The framework SDK we adapt.** Issues in Claude Agent SDK,
  LangChain, OpenAI Agents SDK, CrewAI, Google ADK, or Vercel AI
  SDK are not agentegrity issues. Report them upstream.
- **Detection coverage.** "The regex taxonomy doesn't detect attack
  pattern X" is a feature gap (see STATUS.md), not a vulnerability.
- **agentegrity-pro.** The commercial dashboard ships under a separate
  security policy.

---

## 4. Mitigations summary

| # | Mitigation | Where |
|---|---|---|
| M-1 | Ed25519 record signatures, chain hash links | `src/agentegrity/core/attestation.py` |
| M-2 | Deterministic canonical payload | `AttestationRecord.canonical_payload` |
| M-3 | Atomic file writes | `FileCheckpoint`, `FileBaselineStore` |
| M-4 | Path-traversal guard | `_path_for(agent_id)` in both backends |
| M-5 | Idempotent CREATE TABLE | `SqliteCheckpoint`, `SqliteBaselineStore` |
| M-6 | Persistent connection for `:memory:` | both sqlite backends |
| M-7 | Fail-open exporter fan-out | `_safe_await` / `safeCall` |
| M-8 | Idempotent register_exporter | reference-equality dedup in both Python and TS |
| M-9 | ReDoS-safe regex taxonomy | bounded character classes, no unbounded backrefs |
| M-10 | OIDC trusted publishing | `.github/workflows/release.yml` |
| M-11 | Cross-adapter conformance suite | `tests/test_adapter_conformance.py`, `clients/typescript/test/cross-package-conformance.test.ts` |
| M-12 | Performance budget | `tests/test_perf_budget.py` |
| M-13 | Detection regression gate | `tests/test_benchmarks.py` (synthetic + InjecAgent) |
| M-14 | Tamper-recovery round trip | `tests/test_recovery_restore.py` |

## 5. Open items (v0.7+)

These items have been triaged; none represent a vulnerability against
v0.6.0 but each closes a residual risk above:

- **`KeyProvider` Protocol** with file / env / KMS-backed reference
  impl. Closes T-S1, T-T2 residual risk.
- **JWS / COSE serialization for `AttestationRecord`.** Lets generic
  verifiers validate without depending on the framework. Closes
  interop gap behind T-R1.
- **Per-exporter timeouts.** Closes T-D2 residual risk.
- **Re-enable SLSA provenance + sigstore signatures + SBOM.**
  Disabled when the repo was private; the cogensec move makes them
  eligible again. Closes T-E3.
- **Optional signature envelope on persisted Checkpoint /
  BaselineStore artifacts.** Closes T-T2 residual risk.

---

## 6. Reporting

Report any threat not covered here, or any bypass of a mitigation
listed above, via the process in [`SECURITY.md`](../SECURITY.md).
