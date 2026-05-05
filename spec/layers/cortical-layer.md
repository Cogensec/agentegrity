# Cortical Layer

**Status:** Normative
**Version:** 1.0.0

---

## Purpose

The cortical layer monitors the agent's internal cognitive integrity — reasoning, memory, and behavioral consistency. Named for the cerebral cortex, the brain's executive processing center, this layer protects the higher-order functions that make an agent intelligent and make it dangerous when compromised. It answers one question: **is this agent still itself?**

## Position in Architecture

```
┌─── Adversarial Layer ───┐
│                          │
├─── Cortical Layer ───────┤  ← You are here (middle)
│                          │
├─── Governance Layer ─────┤
│                          │
└──────────────────────────┘
```

The cortical layer evaluates after the adversarial layer passes. It focuses on internal state rather than external threats.

## Components

### Reasoning Validator

Verifies that the agent's reasoning chain is internally consistent, goal-aligned, and free of contradictions. Detects cognitive conflicts where instructions, goals, memory, and planned actions are in tension.

**Inputs:** Reasoning chain, goals, instructions
**Outputs:** ReasoningAssessment (consistency_score, contradictions, goal_alignment, conflict_detected)
**Latency target:** < 20ms p99

### Memory Prover

Tracks the provenance of every memory read and detects integrity violations. Memory from verified internal sources is treated differently from memory retrieved from external or unknown sources.

**Inputs:** Memory read events with provenance metadata
**Outputs:** MemoryAssessment (integrity_score, suspicious_reads, provenance_verified, conflicts_detected)
**Latency target:** < 15ms p99

### Behavioral Monitor

Maintains a behavioral baseline and detects drift — changes in the agent's action distribution, tool usage patterns, response characteristics, or reasoning depth that deviate from established norms.

**Inputs:** Current behavior observation, baseline
**Outputs:** DriftAssessment (drift_score, drifted_dimensions)
**Latency target:** < 10ms p99

**Drift metric.** A conforming implementation MUST use a symmetric, bounded distribution distance for drift scoring. The reference implementation uses Jensen-Shannon distance with Laplace (add-one) smoothing; the result lives in [0, 1] and `d(P, Q) == d(Q, P)`. Asymmetric forward-KL approximations are non-conforming because they make drift verdicts depend on which side of the comparison is treated as "current." A `min_drift_samples` guard (default 20) MUST be honoured: when either distribution has fewer total observations, the dimension is reported as `__insufficient_samples` rather than as drifted/clean.

### Conflict Detector

Identifies contradictions between the agent's stated goals, active instructions, retrieved memory, and planned actions. This is the primary defense against goal hijacking attacks where adversarial instructions silently override the agent's objectives.

**Inputs:** Goals, instructions, memory state, planned actions
**Outputs:** Conflict detection within ReasoningAssessment
**Latency target:** < 25ms p99

### State Attester

Signs and records a snapshot of the agent's internal cognitive state for inclusion in attestation records.

**Inputs:** Current cognitive state
**Outputs:** Signed state snapshot
**Latency target:** < 5ms p99

## Required Interfaces

```python
class CorticalLayer(Protocol):
    def evaluate(self, profile: AgentProfile, context: dict) -> LayerResult: ...
    def validate_reasoning(self, chain: list, goals: list, instructions: list) -> ReasoningAssessment: ...
    def check_memory(self, memory_reads: list[dict]) -> MemoryAssessment: ...
    def detect_drift(self, current_behavior: dict, baseline: BehavioralBaseline) -> DriftAssessment: ...
    def update_baseline(self, observation: dict) -> None: ...
```

## Behavioral Baseline

The baseline is the reference point for drift detection. It must be established during normal (non-adversarial) operation and updated incrementally as the agent's legitimate behavior evolves.

Baseline dimensions:
- **Action distribution**: What actions the agent takes and relative frequency
- **Tool usage patterns**: Which tools are used and how often
- **Response characteristics**: Length, depth, structure
- **Reasoning depth**: Complexity of reasoning chains

Baselines should be re-established after significant changes to the agent's model, system prompt, or tool set.

## Composite Scoring

The cortical layer score is a weighted composite of three dimensions:

```
cortical_score = (reasoning.consistency × 0.35) + (memory.integrity × 0.35) + ((1.0 - drift.score) × 0.30)
```

## Action Determination

| Condition | Action |
|---|---|
| All dimensions healthy | `pass` |
| Drift exceeds tolerance | `alert` |
| Memory integrity below threshold | `alert` |
| Reasoning consistency below threshold | `alert` |
| Cognitive conflict detected | `escalate` |
| Drift exceeds 2× tolerance | `block` |

## Drift Detection

Behavioral drift is measured using approximate KL divergence between the baseline distribution and current observations, normalized to [0, 1]. The default tolerance is 0.15, meaning drift scores above 0.15 trigger alerts.

Drift detection is dimension-specific — the monitor reports *which* behavioral dimensions have drifted, not just that drift occurred. This enables targeted investigation rather than blanket alerts.
