# Recovery Layer

**Status:** Normative
**Version:** 1.0.0

---

## Purpose

The recovery layer verifies the agent's *self-recovery* capability — the third
of the three self-securing properties (after self-defense and self-stability).
It answers one question: **if integrity has been compromised, can the agent
detect that and restore itself to a known-good state?**

Self-defense (the adversarial layer) keeps attacks out. Self-stability (the
cortical layer) detects internal degradation while it happens. Self-recovery
is what runs when both of those are bypassed: it tracks the trajectory of the
agent's integrity over time, verifies the audit trail is intact, and confirms
the agent declares the mechanisms it would need to roll back to a known-good
state.

## Position in Architecture

```
┌─── Adversarial Layer ───┐
│                          │
├─── Cortical Layer ───────┤
│                          │
├─── Governance Layer ─────┤
│                          │
├─── Recovery Layer ───────┤  ← You are here (continuity + degradation watch)
│                          │
└──────────────────────────┘
```

The recovery layer evaluates after governance, sees the cumulative result,
and is the last layer that contributes to the composite score. In
observation mode (`enforce=False`) its `escalate` action is advisory: it
signals that an out-of-band recovery procedure should run, but does not
halt the agent. Under `enforce=True`, `escalate` fails closed like every
other escalate source — the action is denied unless a configured
`approval_handler` approves it. Wire an `approval_handler` if you want
escalations to route to human oversight rather than hard-deny.

## Components

### Baseline Continuity

Verifies that a behavioral baseline exists and is fresh. A `behavioral_baseline`
dict in the evaluation context with `sample_count` and `created_at` keys is
inspected; baselines newer than 24 hours with at least 5 samples score 1.0,
older or smaller baselines score progressively lower.

### Sustained Degradation Detection

Inspects a rolling window of recent composite scores (default 10) and looks
for a sustained drop between the first and second halves of the window. A
drop greater than `degradation_threshold` (default 0.15) raises the
`sustained_degradation` flag and produces an `alert` action. Adapters feed
every composite into the window through `RecoveryLayer.record_score()`;
call it yourself when evaluating outside an adapter.

When the cortical layer holds a learned baseline, adapters pass it to this
layer as `behavioral_baseline`, so the baseline sub-score reflects whether a
restorable baseline exists.

### Recovery Capability Assessment

Counts the recovery mechanisms that have an implementation attached. A
declaration in the profile is not evidence that a mechanism exists, so it
does not raise the score; otherwise any profile could reach the maximum by
listing the right strings.

| Capabilities present | Score |
|---|---|
| 3 or more            | 1.00  |
| 2                    | 0.85  |
| 1                    | 0.70  |
| 0                    | 0.40  |

Today one mechanism has an implementation: a `Checkpoint` backend attached
via `RecoveryLayer(checkpoint=...)` provides `checkpoint`. Recovery
capabilities the profile declares without an implementation
(`state_restore`, `rollback`, `session_reset`, `peer_quarantine`, or
`checkpoint` with no backend) are reported in
`recovery_capabilities_unbacked`.

### Checkpoint Round-Trip

The recovery layer's `snapshot()` / `restore_to()` methods give the
agent a real rollback primitive, not just the ability to *detect*
compromise:

1. `snapshot(agent_id, baseline=None, metadata=None) -> str` — persist
   the chain (every record dict), the rolling score history, an
   optional behavioural baseline, and arbitrary metadata via the
   attached :class:`Checkpoint`. Returns the canonical id.
2. `restore_to(checkpoint_id, *, signing_key=None) -> CheckpointSnapshot`:
   replace `_score_history` with the snapshot's history. The chain is
   evidence and is never rolled back:
   - If the live chain verifies, the snapshot's records MUST be a prefix
     of it (compared by `content_hash`). Every record stays, and a
     `DecisionRecord` with `decision_point="recovery_restore"` is
     appended naming the checkpoint.
   - If the live chain does not verify, the snapshot's records MUST be a
     prefix of it by `record_id`. The full live chain is saved to the
     backend as a snapshot with `metadata["kind"] ==
     "pre_restore_archive"`, the live chain continues from the
     snapshot's records (the same chain object, so an adapter sharing it
     sees the result), and the `recovery_restore` record carries
     `archive_id` and `archived_records`.
   - With no live chain, the snapshot's chain is adopted.
   `restore_to()` refuses an archive, a snapshot whose chain does not
   verify, and a snapshot from another chain's history (`ValueError`).
   After a tamper→restore cycle the layer reports `chain_intact == True`
   and `action != "escalate"` again, and every pre-restore record is
   retrievable from the archive.

The `Checkpoint` Protocol is four methods:

```python
class Checkpoint(Protocol):
    def save(self, snapshot: CheckpointSnapshot) -> str: ...
    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None: ...
    def list_ids(self) -> list[str]: ...
    def latest(self) -> CheckpointSnapshot | None: ...
```

Reference implementations:

| Backend                | Notes |
|------------------------|-------|
| `InMemoryCheckpoint`   | Process-local dict, insertion order preserved. Useful for tests and short-lived agents. |
| `FileCheckpoint(root)` | One `<id>.json` file per snapshot under `root`. Atomic writes (temp file + `os.replace`); path-traversal guard on the id; pretty-printed JSON for jq/grep. |
| `SqliteCheckpoint(path)` | Single `checkpoints` table with payload as JSON. Idempotent `CREATE TABLE IF NOT EXISTS`. `":memory:"` supported via a persistent connection. |

External backends (S3, Redis, KMS-wrapped storage) are pluggable —
implement the four-method Protocol and pass the instance to
`RecoveryLayer(checkpoint=...)`.

### Attestation Chain Continuity

Verifies the supplied `AttestationChain` (if any) using its
`verify_chain()` method. A tampered chain produces `escalate`. An empty or
absent chain scores neutrally (0.5) — the layer is informative, not
punitive, when no continuity history is available.

## Composite Score

The four sub-scores (baseline, degradation, capability, chain) are averaged
into a single `recovery_score`. The default `PropertyWeights` give recovery
0.15 of the composite agentegrity score; raise this in deployments where
agents run long-lived sessions with persistent state.

## Failure Modes

- **No baseline supplied.** Score 0.5 (informative, not failing).
- **No score history.** Sustained-degradation check is skipped; score 1.0.
- **Chain absent.** Continuity check returns 0.5 (no signal either way).
- **Chain tampered.** Action escalates immediately; score collapses to 0.0.

## Configuration

```python
from agentegrity.layers import RecoveryLayer

layer = RecoveryLayer(
    degradation_window=10,
    degradation_threshold=0.15,
    chain=my_attestation_chain,    # optional
    score_history=[0.95, 0.93, ...],  # optional initial history
)
```

## Conformance

A conforming implementation MUST:

1. Expose `record_score(score: float) -> None` to feed scores into the
   degradation window.
2. Accept an optional `AttestationChain` and verify it via
   `verify_chain()`.
3. Produce a `LayerResult` with `layer_name == "recovery"` and a
   `recovery_score` key in `details` so the evaluator's
   `_compute_property_scores` can pick it up.
4. Map a tampered chain to `action == "escalate"`.
5. When a `Checkpoint` backend is attached, expose `snapshot(...)` and
   `restore_to(checkpoint_id)` such that the post-restore chain
   verifies under `verify_chain()` (link hashes are preserved verbatim,
   not re-derived) and no record that existed before the restore is
   lost: it stays in the live chain or is persisted in an archive.
6. Score only recovery capabilities that have an attached
   implementation.

A conforming implementation SHOULD also surface `sustained_degradation`,
`chain_intact`, `recovery_capable`, `recovery_capabilities_present`,
`recovery_capabilities_unbacked`, `checkpoint_count`, and `last_checkpoint_id` in its result `details`
for downstream telemetry.
