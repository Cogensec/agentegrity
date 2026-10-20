# Security Policy

The Agentegrity Framework is an integrity-verification library for
autonomous AI agents. We treat the security of the library itself with
the same standards we expect users to apply to the agents we help
them verify. This document explains how to report a vulnerability,
what we commit to, and which versions are supported.

## Reporting a vulnerability

**Use GitHub Security Advisories — not the public issue tracker.**
[Open a private advisory at
github.com/cogensec/agentegrity/security/advisories/new](https://github.com/cogensec/agentegrity/security/advisories/new).

If GitHub is unavailable to you, email **security@cogensec.com**. Use
PGP if the report contains exploit code or sensitive context — our key
is published at <https://cogensec.com/.well-known/security.txt> and on
the major key servers under the same address.

When you report, please include:

1. The library version (`pip show agentegrity` or `pyproject.toml`)
   and, if relevant, the npm package + version
   (`@agentegrity/<framework>@x.y.z`).
2. A minimal reproduction — a 5-to-50 line script or curl invocation
   that demonstrates the issue. The smaller, the faster we can act.
3. The threat model context: who is the attacker, what trust boundary
   is being crossed, what's the impact (confidentiality, integrity,
   availability)?
4. Any disclosure timeline you need us to respect (regulatory
   deadlines, coordinated disclosure with another vendor, etc.).

## What we commit to

| | |
|---|---|
| Acknowledge receipt | within **2 business days** |
| Triage + initial severity decision | within **5 business days** |
| Status update during fix work | weekly, or sooner on request |
| Fix landed in `main` (high/critical) | within **30 days** of triage |
| Fix landed in `main` (medium/low) | within **90 days** of triage |
| Public advisory + CVE | published with the patched release |

Severity follows [CVSS 3.1](https://www.first.org/cvss/v3.1/specification-document)
on the agentegrity product. Anything that lets an attacker forge a
valid signed `AttestationRecord`, bypass the chain hash check, or
silently disable detection is treated as **critical** by default.

## Coordinated disclosure

We support coordinated disclosure. If you've already reported the
issue to another vendor (e.g. a framework SDK we adapt), tell us in
the report and we'll align our publish timeline with theirs. If you
need credit on the advisory we'll include your handle and (if you
provide them) a contact URL or email.

## Supported versions

The source tree is staged for **v0.13.0** but that version is **not yet
published**. **v0.12.x remains the latest public release until the actual
PyPI and npm publication.** The release-candidate row below states the
support policy that takes effect upon publication; it does not claim
that v0.13.0 is already available.

Pre-1.0, the latest published minor line receives security backports.
v0.12.x becomes EOL only when v0.13.0 is actually published.

| Version | Supported | Notes |
|---|:---:|---|
| 0.13.x | ✅ | Prepared release candidate; support begins **upon publication**. |
| 0.12.x | ⚠️ | Latest publicly released line until v0.13.0 is published; EOL after publication. |
| 0.11.x | ❌ | EOL after the 2026-10-06 v0.12.0 release. |
| < 0.11 | ❌ | Not supported. Upgrade to the latest published minor. |

`scripts/check_versions.py` checks that this table's intended target
matches the source version. Before tagging, replace the candidate wording
with the actual publication state and verify the published distributions.

## Scope

In scope for this policy:

* The Python library (`agentegrity` on PyPI), including every adapter
  (`agentegrity.claude`, `agentegrity.langchain`, etc.).
* The TypeScript packages (`@agentegrity/*` on npm).
* The published JSON Schemas and OpenAPI spec under `schemas/`.
* The reference exporter receiver under `examples/exporter_receiver/`
  (caveat: it is explicitly not a production backend — security
  issues here are accepted and disclosed but treated as
  "documentation defect" severity unless they affect users who took
  the example into production unmodified).
* The CI/release workflow under `.github/workflows/`.

Out of scope:

* The commercial `agentegrity-pro` dashboard (separate product, separate
  security policy).
* Vulnerabilities in framework SDKs we adapt (Claude Agent SDK,
  LangChain, OpenAI Agents, CrewAI, Google ADK, Vercel AI SDK). Report
  those upstream.
* Vulnerabilities in `cryptography`, `anthropic`, or any other transitive
  dependency. Report those to the dependency's project; we'll bump the
  pin when they patch.
* Issues in detection coverage — "the regex taxonomy doesn't catch
  attack pattern X" is a feature gap, not a vulnerability. Open a
  regular issue or PR.

## What's already hardened

We document this here so reporters know which guarantees we're staking
the project on. Bypasses of any of the items below are treated as
critical by default.

* **Attestation chain integrity.** The chain links each record to the
  SHA-256 of the previous record's canonical payload, and
  `AttestationChain.verify_chain()` refuses to validate on any link
  mismatch. Records are signed with Ed25519 only when a signing key is
  configured (`signing_key=` on an adapter, or the per-machine key the
  coding-agent hooks create); without one they are unsigned, and the
  session summary reports `chain_signatures: "unsigned"`. Hash linkage
  alone does not stop someone who holds the chain from rewriting it.
  `AttestationChain.verify_signatures(trusted_keys=...)` and
  `agentegrity verify-decisions --trusted-key` check signatures against
  pinned keys, which is the only check that shows who produced a chain.
* **Deterministic canonical payload.** `AttestationRecord.canonical_payload`
  is `json.dumps(payload, sort_keys=True, separators=(",", ":"))` —
  byte-stable across Python versions for the same input.
* **Atomic writes for persistence.** `FileCheckpoint` and
  `FileBaselineStore` write to a temp file, fsync, then `os.replace`.
  A crash mid-write cannot leave a half-written file readable.
* **Path-traversal guard.** `FileCheckpoint._path_for` and
  `FileBaselineStore._path_for` reject ids containing `/`, `\\`, or
  `..` to prevent escape from the configured root directory.
* **Fail-open exporter fan-out.** A broken `SessionExporter` cannot
  break the instrumented agent — every callback runs through
  `_safe_await` / `safeCall` and exceptions are logged and dropped.
* **Shape-only telemetry.** Anonymous usage telemetry payloads carry only
  enum values, counts, and rounded scores — never prompts, tool
  arguments, names, or agent content. All payload construction is
  centralized and auditable in `src/agentegrity/core/_telemetry_props.py`,
  and the `DO_NOT_TRACK` / `AGENTEGRITY_TELEMETRY_DISABLED` opt-outs are
  honored before any file or network I/O.
* **Tamper recovery.** `RecoveryLayer.restore_to(checkpoint_id)`
  rebuilds the chain from a stored snapshot preserving original link
  hashes — `verify_chain()` returns True post-restore.

The cross-adapter conformance suite
(`tests/test_adapter_conformance.py`,
`clients/typescript/test/cross-package-conformance.test.ts`) and the
detection benchmark (`tests/test_benchmarks.py`) gate every release.

## Threat model

The detailed threat model is in [`spec/threat-model.md`](spec/threat-model.md)
— STRIDE on the framework itself, including what happens if an
exporter is malicious, if the signing key is exfiltrated, or if
policy rules are tampered with at rest.

## See also

* [`STATUS.md`](STATUS.md) — per-component maturity matrix; explicit
  about which subsystems are reference-quality vs hardened.
* [`CHANGELOG.md`](CHANGELOG.md) — every security-relevant change
  lands here under the dated release section.
