# Security Policy

The Agentegrity Framework is an integrity-verification library for
autonomous AI agents. We treat the security of the library itself with
the same standards we expect users to apply to the agents we help
them verify. This document explains how to report a vulnerability,
what we commit to, and which versions are supported.

## Reporting a vulnerability

**Use GitHub Security Advisories — not the public issue tracker.**
[Open a private advisory at
github.com/cogensec/agentegrity-framework/security/advisories/new](https://github.com/cogensec/agentegrity-framework/security/advisories/new).

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

The library is pre-1.0. Until v1.0 lands, only the **latest minor
release line** receives security backports. v0.5 and earlier are no
longer supported.

| Version | Supported | Notes |
|---|:---:|---|
| 0.6.x | ✅ | Current. All security fixes ship here first. |
| 0.5.x | ❌ | EOL on the 2026-05-05 v0.6.0 release. Upgrade. |
| < 0.5 | ❌ | Never supported in security backports. |

After v1.0 we will move to a dual-track support window: latest minor
+ previous minor for at least 6 months. That policy will be
documented in this file when v1.0 ships.

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

* **Attestation chain integrity.** Every `AttestationRecord` is signed
  with Ed25519, and the chain links each record to the SHA-256 of the
  previous record's canonical payload. `AttestationChain.verify_chain()`
  walks the full chain and refuses to validate on any link mismatch.
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
