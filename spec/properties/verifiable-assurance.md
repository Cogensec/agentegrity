# Property 3: Verifiable Assurance

**Status:** Normative
**Version:** 1.0.0

---

## Definition

An agent possesses verifiable assurance when its integrity state is cryptographically provable — not merely observable. Integrity assessments produce attestation records that are independently verifiable, tamper-evident, and auditable.

Formally: the agent's integrity state is represented by an attestation record R satisfying four conditions:

```
1. Completeness:    R contains sufficient evidence to reconstruct the integrity evaluation
2. Tamper-evidence: Any modification to R is detectable via cryptographic verification
3. Non-repudiation: R is signed such that the evaluator cannot deny producing it
4. Chain integrity: R references the previous attestation, forming a verifiable history
```

## What It Prevents

| Risk | Description |
|---|---|
| Undetected compromise | Agent was compromised but no record proves it was ever verified |
| Audit gaps | Periods where the agent operated without integrity evaluation |
| Retroactive tampering | Integrity records modified after the fact to conceal violations |
| Trust assumptions | "We checked and it looked fine" — observational, not provable |
| Repudiation | Evaluator denies the integrity assessment it produced |

## Attestation Record Format

Every attestation record contains:

| Field | Type | Description |
|---|---|---|
| `record_id` | UUID | Unique identifier for this record |
| `agent_id` | string | The agent this attestation covers |
| `timestamp` | datetime (UTC) | When the evaluation occurred |
| `integrity_score` | IntegrityScore | Full composite and per-property scores |
| `layer_states` | dict | Per-layer evaluation results |
| `evidence` | list[Evidence] | Supporting evidence chain |
| `chain_previous` | string (nullable) | Hash of the previous attestation record |
| `signature` | bytes | Ed25519 signature over the canonical payload |
| `public_key` | bytes | Signing key for independent verification |

### Canonical Payload

The canonical payload is the deterministic JSON serialization of the record (sorted keys, no whitespace) used for both signing and hash computation. This ensures that any two implementations produce identical payloads for identical records.

### Evidence Objects

Each evidence item provides a link in the proof chain:

| Field | Type | Description |
|---|---|---|
| `evidence_type` | string | "layer_result", "validator_output", or "external" |
| `source` | string | Which component produced this evidence |
| `content_hash` | string | SHA-256 hash of the evidence content |
| `summary` | string | Human-readable summary |
| `timestamp` | datetime | When the evidence was produced |

## Evaluation Methodology

### Step 1: Attestation Generation

After every integrity evaluation, produce a signed attestation record. The record must include the full integrity score, all layer results, and supporting evidence.

### Step 2: Chain Linking

Each new attestation record references the content hash of the previous record, forming an append-only chain.

> **Hash linkage is necessary but NOT sufficient for tamper-evidence.** `content_hash` is an *unkeyed* SHA-256. An attacker who controls the serialized chain can edit any record and recompute every forward link, producing a chain that passes hash-link verification (`verify_chain()`). Hash linkage detects accidental corruption and missing intermediate records; it does **not** detect adversarial modification on its own. Tamper-evidence against an adversary requires Step 3 (signature verification against a trusted key).

### Step 3: Independent Verification

A third party must be able to verify any attestation record using only:
- The attestation record itself
- **A trusted (pinned) copy of the evaluator's public key**

No access to the evaluator's infrastructure, the agent, or any other system should be required.

> **The embedded `public_key` field is not a trust anchor.** A record carries its own public key for convenience, but verifying a record's signature against the key embedded *in that same record* proves nothing: a forger generates their own keypair, signs every record, and embeds their public key, so the chain self-verifies. A conformant verifier MUST verify signatures (`verify_signatures(trusted_keys=...)`) against a public key obtained out-of-band and MUST reject records whose `public_key` is not in the pinned set. Verifying only hash linkage, or verifying signatures without a pinned key, does not satisfy condition 2 (Tamper-evidence) or condition 3 (Non-repudiation).

## Signing

The reference implementation uses Ed25519 signatures. Implementations may use other asymmetric signing algorithms provided they meet equivalent security guarantees (minimum 128-bit security level).

Key management is the implementer's responsibility. The specification requires that signing keys are stored securely and rotated per organizational policy. Key rotation creates a new attestation chain segment, linked to the previous segment via a key rotation record.

## Scoring Bands

| Score | Band | Interpretation |
|---|---|---|
| 0.90–1.00 | Strong | Full attestation chain; all records independently verifiable; no gaps |
| 0.70–0.89 | Adequate | Attestation operational; minor gaps in coverage or chain |
| 0.50–0.69 | Degraded | Partial attestation; some evaluations unattested |
| Below 0.50 | Insufficient | No cryptographic assurance; integrity claims are observational only |

## Required Controls

| Control ID | Description | Conformance Level |
|---|---|---|
| VA-01 | Attestation records generated for every integrity evaluation | Level 2 |
| VA-02 | Ed25519 (or equivalent) signing of attestation records | Level 3 |
| VA-03 | Attestation chain integrity maintained (hash linking) | Level 3 |
| VA-04 | Independent verification endpoint or tool available | Level 3 |
| VA-05 | Attestation records retained per organizational policy (minimum 90 days) | Level 3 |

## Relationship to Other Properties

- **Adversarial Coherence**: Every coherence evaluation produces evidence that feeds into attestation records.
- **Environmental Portability**: Attestation records must include the environment identifier to enable cross-environment auditability.
