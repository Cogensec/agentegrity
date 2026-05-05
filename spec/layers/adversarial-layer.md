# Adversarial Layer

**Status:** Normative
**Version:** 1.0.0

---

## Purpose

The adversarial layer is the outermost integrity defense. It continuously tests and validates the agent's resilience to attack across all input channels. It answers one question: **can this agent be broken right now?**

## Position in Architecture

```
┌─── Adversarial Layer ───┐  ← You are here (outermost)
│                          │
├─── Cortical Layer ───────┤
│                          │
├─── Governance Layer ─────┤
│                          │
└──────────────────────────┘
```

The adversarial layer evaluates first. If it detects a critical threat, it can block execution before the cortical and governance layers run.

## Components

### Attack Surface Mapper

Enumerates all input channels and interfaces through which adversarial content can reach the agent. The map is derived from the agent's `AgentProfile` and updated whenever capabilities change.

**Inputs:** AgentProfile
**Outputs:** AttackSurfaceMap (channels, tool interfaces, memory surfaces, peer interfaces, total surface area)
**Frequency:** On profile change and periodically (recommended: hourly)

### Threat Detector

Real-time detection of adversarial inputs across all channels. The reference implementation ships a regex-pattern taxonomy of 21 default patterns organised into six attack families (`prompt_injection`, `jailbreak`, `role_confusion`, `system_prompt_extraction`, `data_exfiltration`, `prompt_obfuscation`). Each pattern carries a calibrated `severity` and `confidence`; matches are aggregated into one `ThreatAssessment` per (channel, threat_type) pair, with `indicators` listing every pattern that fired. Patterns scan direct input *plus* `memory_reads[*].content` *plus* `tool_outputs[*].content` so model-context and model-in-the-middle attacks are visible to the layer.

Patterns are extensible:

- `AdversarialLayer(extra_patterns=[...])` — append to the default taxonomy.
- `AdversarialLayer(patterns=[...])` — replace the taxonomy wholesale (use `default_detector_patterns()` as a starting point).
- `AdversarialLayer(threat_detectors=[fn])` — register imperative detector callables that receive `(profile, context)` and return `list[ThreatAssessment]`.

Production deployments should layer in domain-specific patterns and, when available, an embedding-similarity classifier or LLM-backed semantic detector (planned for v0.6.0).

**Inputs:** Input data, channel identifier, AgentProfile
**Outputs:** ThreatAssessment (channel, threat_type, severity, confidence, indicators)
**Latency target:** < 10ms p99

### Coherence Scorer

Computes the adversarial coherence score by measuring behavioral deviation from baseline under adversarial pressure. The score degrades proportionally to threat severity and confidence.

**Inputs:** AgentProfile, list of ThreatAssessments, context
**Outputs:** float (0.0 - 1.0)
**Latency target:** < 50ms p99

### Red Team Harness

Interface for automated and manual adversarial testing. Accepts scenario definitions and runs them against the agent, producing structured results.

**Inputs:** AgentProfile, adversarial scenario
**Outputs:** RedTeamResult
**Frequency:** Async, scheduled (recommended: daily for Level 2, continuous for Level 3)

### Threat Intel Connector

Ingests emerging attack patterns from external threat intelligence sources. Updates the threat detector's pattern library.

**Inputs:** Threat intelligence feeds
**Outputs:** Updated detection patterns
**Frequency:** Async, polling (recommended: every 6 hours)

## Required Interfaces

```python
class AdversarialLayer(Protocol):
    def evaluate(self, profile: AgentProfile, context: dict) -> LayerResult: ...
    def detect(self, input_data: Any, channel: str) -> ThreatAssessment | None: ...
    def map_attack_surface(self, profile: AgentProfile) -> AttackSurfaceMap: ...
    def red_team(self, profile: AgentProfile, scenario: dict) -> RedTeamResult: ...
```

## Action Determination

| Condition | Action |
|---|---|
| No threats detected | `pass` |
| Threats detected, all severity < 0.50 | `pass` (with threat details in result) |
| Threats detected, max severity 0.50–0.89 | `alert` |
| Any threat severity ≥ 0.90 (and `block_on_critical=True`) | `block` |
| Coherence score below threshold | `alert` |

## Extensibility

Register custom threat detectors via the constructor:

```python
def my_detector(profile: AgentProfile, context: dict) -> list[ThreatAssessment]:
    # Domain-specific threat detection logic
    ...

layer = AdversarialLayer(
    coherence_threshold=0.85,
    threat_detectors=[my_detector]
)
```
