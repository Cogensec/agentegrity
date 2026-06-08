# Property: Decision Provenance

**Status:** Normative (introduced in v0.7)
**Version:** 0.1.0

---

## Definition

An agent exhibits **decision provenance** when, for every decision boundary it crosses, it produces a signed, hash-chained record that binds the candidate action to the inputs and reasoning that justified it **before** the action executes.

Formally: for each decision point d in the set {pre_tool_use, stop, subagent_start}, an agent must append a `DecisionRecord` r_d to its `AttestationChain` such that:

1. r_d.timestamp < execution-time(d.action)
2. r_d.candidate_action canonically describes the action that will be attempted
3. r_d.decision_inputs commits to the input channels (hash + summary) that the agent observed at decision time
4. r_d is included under r_d.signature when a signing key is configured
5. The subsequent `AttestationRecord` includes an `Evidence(evidence_type="decision", source=r_d.record_id, content_hash=r_d.content_hash)` entry, cryptographically committing the evaluator's verdict to the decisions that preceded it

Decision provenance closes the gap between **what** the evaluator concluded (`AttestationRecord`) and **why** the agent decided (`DecisionRecord`). Without it, an adversary who controls the agent post-hoc can rewrite the rationale to make any action look justified.

## What It Prevents

| Attack class | Description | Prevented when |
|---|---|---|
| Post-hoc rationalization | Adversary fabricates a clean explanation for a malicious action after the fact | Records are signed AND verified by an independent party |
| Decision-rationale tampering | Adversary modifies stored rationale to mislead audit | Tampering invalidates the signature; `verify_decision_links()` detects content-hash mismatch |
| Audit-trail gaps | Adversary deletes records to hide actions | Chain hash linkage (`verify_chain()`) detects missing intermediate records |

## What It Does NOT Prevent

The honest scope of this control:

- **Pre-decision manipulation**: if the agent's reasoning was already corrupted by upstream attack (prompt injection, memory poisoning, tool-output poisoning), the decision record will faithfully attest a corrupt decision. Decision provenance proves the rationale was bound at decision time, not that the rationale was correct.
- **Capture suppression**: the capture path is fail-open. An adversary who can trigger an exception inside `record_decision` (e.g., crafted `candidate_action` that defeats the `_json_safe` coercer) leaves a chain gap. The framework emits a structured `capture_failure` `FrameworkEvent` so monitoring can detect the gap, but the action still proceeds. Fail-closed would crash agents on transient bugs — a worse trade-off for adoption.
- **Key compromise**: an adversary in possession of the signing key can forge clean records. Mitigated by HSM / KMS-bound keys, out of scope for this property.

## Capture Tiers

The `CaptureTier` enum quantifies how much rationale was actually captured at a boundary. Tier is inferred from which rationale fields the adapter populates:

| Tier | Symbol | Required fields | Production status (v0.7) |
|---|---|---|---|
| C (Minimal) | `MINIMAL` | candidate_action + decision_inputs | All shipped adapters today |
| B (Partial) | `PARTIAL` | + reasoning_chain | No adapter populates this in production; tested via fixture |
| A (Full) | `FULL` | + rejected_alternatives | No adapter populates this in production; tested via fixture |

Tier A and B unlock as adapter-specific deliberation surfaces are wired (Claude Agent SDK reasoning streams, OpenAI Responses reasoning content, etc.). The schema and verification path are tier-agnostic; today's records are Tier C, and the spec is honest about that.

## Decision Boundaries

The three boundaries `_BaseAdapter` captures, and what each means:

### `pre_tool_use`

Captured **between** the integrity evaluation and the enforcement check. The agent has decided to call a tool; the framework records the tool name + arguments + decision inputs before the call executes (and before any enforce-mode block fires). Even blocked tool calls leave a record — the audit trail shows what the agent attempted, not just what it succeeded at.

### `stop`

Captured before the `stop` event fans out to exporters. The candidate action is the final output the agent is about to return; the record commits to a SHA-256 of the output content plus a short summary. Adapters with thin `stop` data (Claude passes `{}`) produce a record with the SHA-256 of an empty string — Tier C with no useful content, but still a chain anchor.

### `subagent_start`

A category-honest framing. `subagent_start` fires when the **child** starts running; the parent's decision to delegate already happened earlier (typically at the parent's own `pre_tool_use` if the subagent is invoked as a tool). The record's `candidate_action.type` is therefore `"subagent_dispatch_observed"` (not `"handoff_decision"`), and `boundary_category` is `"lifecycle_attestation"`. A downstream verifier reading the record can tell this is post-decision lifecycle data, not a primary decision the parent made.

Only adapters with genuine subagent semantics (Agno teams, AWS Bedrock collaborators) emit `subagent_start` in normal operation.

## Required Controls

A conforming implementation MUST:

1. Append a `DecisionRecord` to the chain at every supported decision boundary the framework exposes.
2. Build the record **before** any side-effect of the decision (the action executes, the output is returned, the subagent is dispatched).
3. Include `chain_previous` in the canonical payload so the signature covers the chain link.
4. When a signing key is configured at adapter construction, sign every decision record with that key.
5. Provide a defensive `_json_safe` coercer for non-JSON-native types in `candidate_action` so capture cannot crash on exotic payloads.
6. On capture exception, emit a `capture_failure` `FrameworkEvent` with `{decision_point, exception_class, summary}` and continue without raising.
7. Include `Evidence(evidence_type="decision", source=record_id, content_hash=...)` entries on each `AttestationRecord` for every decision appended since the previous attestation.

A conforming verifier MUST:

1. Validate `chain.verify_chain()` — record-to-record hash linkage.
2. Validate `chain.verify_decision_links()` — every attestation's decision-type Evidence points at an existing, unaltered, temporally-prior decision in the chain.
3. When records are signed, validate `record.verify()` for each one.

## Relationship to Other Properties

- **Verifiable Assurance (VA):** decision provenance is a structural input to VA. A future VA refactor (out of scope for v0.7) will score signed decision records as evidence in the assurance composite. The hook will sit in the property-measurement layer, which today only sees a context dict and layer results.
- **Adversarial Coherence (AC):** decision records make AC violations auditable after the fact. If the AC layer flags a coherence break, the corresponding decision record carries the specific candidate action that triggered the break, with cryptographic proof the agent considered exactly that action and not something downstream-rewritten.
- **Recovery Integrity (RI):** the chain serialization (`to_json` / `from_json`) integrates with the checkpoint path. Restoring a checkpoint restores the decision chain alongside the attestation chain; `verify_chain()` + `verify_decision_links()` validate both after restore.

## Backward Compatibility

The v0.7 change that adds `record_kind` to `AttestationRecord.canonical_payload` (so the chain can carry both record kinds discriminably) shifts the `content_hash` of every newly-built attestation. Consequence:

**Chains serialized before v0.7 fail `verify_chain()` after upgrade**, signed or not. The integrity check compares each record's recomputed `content_hash` against the next record's stored `chain_previous`; the new code computes hashes over a different canonical payload. Loading still works; verification doesn't.

There is no rescue migration script. Operators with on-disk signed chains must either re-build the chain from a fresh root with the new code or pin to the pre-v0.7 version. The chain remains historically useful but is no longer cryptographically verifiable across the v0.6 → v0.7 boundary.

This is documented in CHANGELOG.md under v0.7's `Changed` section.
