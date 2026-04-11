# Agentegrity Framework Specification

**Version 0.1.0**
**Status: Draft**
**Date: March 2026**

---

## 1. Overview

This specification defines the Agentegrity Framework — a standard for measuring, enforcing, and proving the integrity of autonomous AI agents. It is the technical companion to the [Agentegrity Manifesto](../MANIFESTO.md).

### 1.1 Scope

This specification covers:
- Formal definitions of the three agentegrity properties
- Architecture of the three integrity layers
- Required and optional controls at each layer
- Integrity scoring methodology
- Attestation record format
- Maturity model for progressive adoption

This specification does not cover:
- Agent alignment or value alignment
- Model training safety
- Data governance (except as it intersects agent memory integrity)
- Specific vendor implementations

### 1.2 Conformance Levels

| Level | Name | Requirements |
|---|---|---|
| **Level 1** | Aware | Agent profile defined; adversarial layer active; integrity scoring operational |
| **Level 2** | Managed | All three layers active; continuous monitoring; policy enforcement |
| **Level 3** | Verified | Cryptographic attestation; formal property verification; automated red teaming |
| **Level 4** | Autonomous | Self-healing integrity; adaptive policy; cross-environment portability proven |

---

## 2. Core Abstractions

### 2.1 Agent Profile

Every agent under agentegrity evaluation is described by an `AgentProfile`:

```
AgentProfile:
  agent_id:           string        # Unique identifier
  agent_type:         enum          # conversational | tool_using | autonomous | multi_agent | embodied
  capabilities:       list[string]  # tool_use, memory_access, multi_agent_comm, code_execution, web_access, physical_actuation
  deployment_context: enum          # cloud | edge | hybrid | multi_agent | physical
  risk_tier:          enum          # low | medium | high | critical
  framework:          string        # Optional: langchain, crewai, autogen, custom, etc.
  model_provider:     string        # Optional: openai, anthropic, google, open_source, etc.
  metadata:           dict          # Extensible metadata
```

### 2.2 Integrity Score

The composite integrity score is a weighted aggregation of the three property scores:

```
IntegrityScore:
  composite:          float[0.0, 1.0]
  adversarial_coherence:    float[0.0, 1.0]
  environmental_portability: float[0.0, 1.0]
  verifiable_assurance:      float[0.0, 1.0]
  timestamp:          datetime
  confidence:         float[0.0, 1.0]
  evaluator_version:  string
```

Default weights: `adversarial_coherence=0.40, environmental_portability=0.25, verifiable_assurance=0.35`. Weights are configurable per deployment.

### 2.3 Attestation Record

An attestation record is the cryptographic proof of an agent's integrity state at a point in time:

```
AttestationRecord:
  record_id:          string        # UUID
  agent_id:           string        # Reference to AgentProfile
  timestamp:          datetime      # UTC timestamp
  integrity_score:    IntegrityScore
  layer_states:       dict          # Per-layer evaluation results
  evidence:           list[Evidence] # Supporting evidence chain
  signature:          bytes         # Ed25519 signature over record contents
  public_key:         bytes         # Signing key for verification
  chain_previous:     string        # Hash of previous attestation (chain integrity)
```

---

## 3. Property Specifications

### 3.1 Adversarial Coherence

**Formal definition:** An agent A possesses adversarial coherence at time t if, for a defined set of adversarial perturbations P applied across all input channels C, the agent's decision function D produces outputs within tolerance ε of its baseline behavior B:

```
∀p ∈ P, ∀c ∈ C: distance(D(input + p_c), B) ≤ ε
```

**Input channels (C):**
- Direct prompts and instructions
- Tool call responses
- Retrieved documents (RAG)
- Inter-agent messages
- Environmental signals (for embodied agents)
- Memory reads

**Evaluation methods:**
1. **Baseline establishment:** Record agent behavior across a standardized evaluation suite under clean conditions
2. **Perturbation injection:** Apply adversarial perturbations per channel and measure behavioral deviation
3. **Coherence scoring:** Compute the ratio of perturbation scenarios where behavior remains within tolerance

**Required controls:**
- `AC-01`: Baseline behavioral profile established and versioned
- `AC-02`: Per-channel adversarial evaluation coverage
- `AC-03`: Coherence score computed at minimum daily frequency
- `AC-04`: Threshold alerts on coherence degradation
- `AC-05`: Automated adversarial test suite (red teaming)

**Scoring:**
- 0.90–1.00: Agent maintains coherence under all tested perturbations
- 0.70–0.89: Agent maintains coherence under most perturbations; isolated deviations
- 0.50–0.69: Agent shows measurable coherence degradation under moderate pressure
- Below 0.50: Agent integrity cannot be assured under adversarial conditions

See [Adversarial Coherence property spec](properties/adversarial-coherence.md) for full detail.

### 3.2 Environmental Portability

**Formal definition:** An agent A possesses environmental portability if its integrity score S is equivalent (within tolerance δ) across a defined set of deployment environments E:

```
∀e_i, e_j ∈ E: |S(A, e_i) - S(A, e_j)| ≤ δ
```

**Deployment environments (E):**
- Single-tenant cloud (isolated)
- Multi-tenant cloud (shared infrastructure)
- Edge / on-premise (resource-constrained)
- Multi-agent system (peer agents present)
- Federated (cross-organizational)
- Physical (embodied, real-world actuation)

**Evaluation methods:**
1. **Environment matrix:** Define the set of target deployment environments
2. **Cross-environment evaluation:** Run the full integrity evaluation in each environment
3. **Portability scoring:** Compute the variance of integrity scores across environments

**Required controls:**
- `EP-01`: Target deployment environments enumerated
- `EP-02`: Per-environment integrity evaluation executed
- `EP-03`: Environment-specific threat models maintained
- `EP-04`: Trust boundary definitions per environment
- `EP-05`: Portability variance tracked over time

**Scoring:**
- 0.90–1.00: Integrity scores consistent (δ ≤ 0.05) across all environments
- 0.70–0.89: Minor variance (δ ≤ 0.15) with documented environment-specific mitigations
- 0.50–0.69: Significant variance; integrity degrades in some environments
- Below 0.50: Integrity guarantees do not port across environments

See [Environmental Portability property spec](properties/environmental-portability.md) for full detail.

### 3.3 Verifiable Assurance

**Formal definition:** An agent A possesses verifiable assurance if its integrity state is represented by an attestation record R that satisfies:

```
1. Completeness: R contains sufficient evidence to independently reconstruct the integrity evaluation
2. Tamper-evidence: Any modification to R is detectable via cryptographic verification
3. Non-repudiation: R is signed such that the evaluator cannot deny producing it
4. Chain integrity: R references the previous attestation, forming a verifiable history
```

**Evaluation methods:**
1. **Attestation generation:** Produce a signed attestation record after each integrity evaluation
2. **Independent verification:** A third party can verify the attestation using only the record and the evaluator's public key
3. **Chain validation:** The full attestation history is verifiable as an unbroken, unmodified chain

**Required controls:**
- `VA-01`: Attestation records generated for every integrity evaluation
- `VA-02`: Ed25519 (or equivalent) signing of attestation records
- `VA-03`: Attestation chain integrity maintained (hash linking)
- `VA-04`: Independent verification endpoint or tool available
- `VA-05`: Attestation records retained per organizational policy (minimum 90 days)

**Scoring:**
- 0.90–1.00: Full attestation chain; all records independently verifiable; no gaps
- 0.70–0.89: Attestation operational; minor gaps in coverage or chain
- 0.50–0.69: Partial attestation; some evaluations unattested
- Below 0.50: No cryptographic assurance; integrity claims are observational only

See [Verifiable Assurance property spec](properties/verifiable-assurance.md) for full detail.

---

## 4. Layer Architecture

### 4.1 Adversarial Layer

**Purpose:** Continuously test and validate the agent's resilience to attack.

**Position:** Outermost layer. First line of integrity defense.

**Components:**

> These are design targets for v1.0 of the specification. The v0.1.0 reference implementation does not yet measure or enforce these targets.

| Component | Function | Latency Target |
|---|---|---|
| Attack Surface Mapper | Enumerate all input channels and tool interfaces | Async (background) |
| Threat Detector | Real-time detection of adversarial inputs across channels | < 10ms p99 |
| Coherence Scorer | Compute adversarial coherence score against baseline | < 50ms p99 |
| Red Team Harness | Automated adversarial testing interface | Async (scheduled) |
| Threat Intel Connector | Ingest emerging attack patterns | Async (polling) |

**Required interfaces:**
- `evaluate(agent_profile, context) → AdversarialResult`
- `detect(input, channel) → ThreatAssessment`
- `red_team(agent_profile, scenario) → RedTeamResult`

See [Adversarial Layer spec](layers/adversarial-layer.md) for full detail.

### 4.2 Cortical Layer

**Purpose:** Monitor and validate the agent's internal cognitive integrity.

**Position:** Middle layer. Protects reasoning, memory, and behavioral consistency.

**Components:**

> These are design targets for v1.0 of the specification. The v0.1.0 reference implementation does not yet measure or enforce these targets.

| Component | Function | Latency Target |
|---|---|---|
| Reasoning Validator | Verify reasoning chain consistency and goal alignment | < 20ms p99 |
| Memory Prover | Track memory provenance and detect poisoning | < 15ms p99 |
| Behavioral Monitor | Maintain baselines and detect drift | < 10ms p99 |
| Conflict Detector | Identify contradictions between goals, instructions, memory, actions | < 25ms p99 |
| State Attester | Sign and record internal cognitive state | < 5ms p99 |

**Required interfaces:**
- `evaluate(agent_profile, cognitive_state) → CorticalResult`
- `validate_reasoning(chain) → ReasoningAssessment`
- `check_memory(read_event) → MemoryAssessment`
- `detect_drift(current_behavior, baseline) → DriftAssessment`

See [Cortical Layer spec](layers/cortical-layer.md) for full detail.

### 4.3 Governance Layer

**Purpose:** Enforce organizational policy, oversight, and compliance requirements.

**Position:** Innermost layer. Closest to the agent's action execution.

**Components:**

> These are design targets for v1.0 of the specification. The v0.1.0 reference implementation does not yet measure or enforce these targets.

| Component | Function | Latency Target |
|---|---|---|
| Policy Engine | Evaluate actions against policy-as-code rules | < 5ms p99 |
| Escalation Manager | Route high-risk decisions to human oversight | < 100ms p99 |
| Compliance Mapper | Map integrity evaluations to regulatory frameworks | Async |
| Audit Logger | Produce immutable, signed audit records | < 2ms p99 |
| Break-Glass Controller | Emergency override and agent suspension | < 10ms p99 |

**Required interfaces:**
- `evaluate(agent_profile, action) → GovernanceResult`
- `enforce_policy(action, policy_set) → PolicyDecision`
- `escalate(action, risk_assessment) → EscalationResult`
- `emergency_stop(agent_id, reason) → StopConfirmation`

See [Governance Layer spec](layers/governance-layer.md) for full detail.

---

## 5. Integrity Evaluation Flow

The standard evaluation flow processes an agent action through all three layers:

```
Agent Action Initiated
        │
        ▼
┌─── Adversarial Layer ───┐
│ 1. Threat detection      │
│ 2. Input channel scan    │
│ 3. Coherence check       │
│                          │
│ → BLOCK if threat score  │
│   exceeds threshold      │
└──────────┬───────────────┘
           │ PASS
           ▼
┌─── Cortical Layer ───────┐
│ 4. Reasoning validation  │
│ 5. Memory provenance     │
│ 6. Behavioral drift      │
│ 7. Conflict detection    │
│                          │
│ → ALERT if drift exceeds │
│   tolerance              │
└──────────┬───────────────┘
           │ PASS
           ▼
┌─── Governance Layer ─────┐
│ 8. Policy evaluation     │
│ 9. Risk tier assessment  │
│ 10. Compliance check     │
│ 11. Audit log write      │
│                          │
│ → ESCALATE if policy     │
│   requires human review  │
└──────────┬───────────────┘
           │ PASS
           ▼
   Action Executed
           │
           ▼
  Attestation Record Generated
```

**Total latency budget:** < 100ms p99 for the full evaluation flow. (This is a design target for v1.0 of the specification. The v0.1.0 reference implementation does not yet measure or enforce this target.)

---

## 6. Maturity Model

### Level 1: Aware

The organization has adopted agentegrity vocabulary and tooling. Agents are profiled and basic integrity scoring is operational.

| Requirement | Controls |
|---|---|
| Agent profiles defined for all deployed agents | AgentProfile schema populated |
| Adversarial layer active | AC-01, AC-02 |
| Integrity scores computed | Composite score generated |
| Baseline behaviors established | AC-01 |

### Level 2: Managed

All three layers are active. Continuous monitoring is operational. Policies are enforced.

| Requirement | Controls |
|---|---|
| All three layers deployed | All layer interfaces implemented |
| Continuous integrity monitoring | Runtime evaluation on every action |
| Policy-as-code enforcement | Governance layer policies defined and active |
| Human escalation paths defined | Escalation Manager configured |
| Audit trails operational | Audit Logger active |

### Level 3: Verified

Cryptographic attestation is operational. Properties are formally verified. Automated red teaming runs continuously.

| Requirement | Controls |
|---|---|
| Attestation records generated | VA-01, VA-02, VA-03 |
| Independent verification available | VA-04 |
| Automated red teaming | AC-05 |
| Cross-environment evaluation | EP-01, EP-02 |
| Formal property verification | Mathematical property proofs |

### Level 4: Autonomous

Integrity is self-maintaining. The system adapts to new threats, new environments, and new policies without manual intervention.

| Requirement | Controls |
|---|---|
| Self-healing integrity responses | Automated remediation on degradation |
| Adaptive policy engine | Policies adjust to context and risk |
| Cross-environment portability proven | EP scores consistent across all targets |
| Continuous adversarial adaptation | Threat models update from live data |
| Full attestation chain history | VA-05 with complete chain |

---

## 7. Extensibility

The framework is designed for extension:

- **Custom validators:** Implement the `Validator` interface to add domain-specific integrity checks
- **Custom layers:** Additional layers can be inserted between the three standard layers
- **Custom scoring:** Property weights and scoring functions are configurable
- **Custom attestation:** Attestation format supports extension fields for domain-specific evidence
- **Framework adapters:** Implement the `AgentAdapter` interface to integrate with any agent framework

---

## 8. Versioning

This specification follows semantic versioning:
- **Major:** Breaking changes to core abstractions, properties, or layer architecture
- **Minor:** New controls, validators, or conformance requirements
- **Patch:** Clarifications, typo fixes, editorial changes

---

*Agentegrity Framework Specification v1.0.0 · Cogensec Research · March 2026*
