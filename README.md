# Agentegrity Framework

**Building AI agents capable of securing themselves.**

Every existing AI security tool builds protection that humans apply to agents from the outside. Guardrails filter inputs. Runtime monitors watch outputs. Policy engines enforce rules. These are necessary, and Agentegrity does not replace them. Agentegrity addresses a different question: how do you measure whether the agent itself has the structural integrity to remain coherent when those external controls cannot reach inside its decision process?

Agentegrity (agent + integrity) is the discipline of building AI agents that can defend themselves, stabilize themselves, and recover themselves — and then verifying that they actually can. This repository provides the open specification, the reference architecture, and a Python implementation for that verification.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Library Version](https://img.shields.io/badge/library-v0.1.0-orange.svg)](pyproject.toml)
[![Spec Version](https://img.shields.io/badge/spec-v1.0--draft-blue.svg)](spec/SPECIFICATION.md)

---

## Why This Matters Now

Frontier model labs ship better base models on a regular cadence. Each new release reduces the rate at which the underlying model produces unsafe outputs in isolated benchmarks. This is real progress, and it does not solve the agent security problem.

Enterprises do not deploy base models. They deploy compositions: a base model wrapped in system prompts, augmented with retrieval over private data, given access to tools that touch customer systems, equipped with persistent memory, orchestrated through planning loops, and embedded in environments that produce inputs the model was never trained against. Every capability gain in the underlying model enables more ambitious compositions with more attack surface. The composition layer is where security failures occur, and the composition layer is not what the model labs are improving.

Agentegrity is positioned at the composition layer specifically. Its measurements are about whether the assembled agent — not the underlying model — has the structural properties required to maintain integrity under adversarial pressure, across deployment contexts, and over time.

---

## The Three Self-Securing Capabilities

A self-securing agent maintains three properties simultaneously. Each property is a capability the agent has, not a control imposed on it from outside. The Agentegrity Framework defines how to verify each one.

| Capability | What The Agent Does | What This Prevents |
|---|---|---|
| **Self-Defense** | Maintains coherent reasoning under adversarial pressure across all input channels | Goal hijacking, prompt injection, indirect injection via retrieved content, tool output poisoning |
| **Self-Stability** | Monitors its own behavioral drift against an established baseline and detects internal state corruption | Slow-drift attacks, memory poisoning, gradual goal redirection, identity erosion |
| **Self-Recovery** | Detects when its integrity has been compromised and restores itself to a known-good state | Persistent compromise, undetected lateral movement, state pollution across sessions |

This release (v0.1.0) ships verification for self-defense and self-stability through the adversarial and cortical layers. Self-recovery verification is on the v0.2 roadmap.

---

## The Three Layers

The framework implements verification through three architectural layers. Each layer addresses a different dimension of integrity. Together they form a complete envelope around the agent.

```
┌─────────────────────────────────────────────┐
│           GOVERNANCE LAYER                  │
│   Policy enforcement · Human oversight ·    │
│   Compliance mapping · Audit trails         │
├─────────────────────────────────────────────┤
│            CORTICAL LAYER                   │
│   Reasoning consistency · Memory checks ·   │
│   Behavioral baselines · Drift detection    │
├─────────────────────────────────────────────┤
│           ADVERSARIAL LAYER                 │
│   Attack surface mapping · Threat           │
│   detection · Coherence scoring             │
└─────────────────────────────────────────────┘
```

The **Adversarial Layer** verifies self-defense by mapping the agent's attack surface and detecting threats across input channels. The **Cortical Layer** verifies self-stability by monitoring reasoning consistency, memory integrity, and behavioral drift from baseline. The **Governance Layer** enforces organizational policy and produces audit trails so verification results have a place to live in compliance workflows.

---

## What This Library Does (and Does Not)

We believe in being explicit about what the library is and is not, because a security library that overpromises is worse than one that underdelivers.

**What it does.** It provides a Python implementation of the three-layer verification architecture defined in the [Agentegrity Specification](spec/SPECIFICATION.md). It computes integrity scores from real evaluation runs, generates cryptographically signed attestation records, builds tamper-evident attestation chains, and produces structured audit logs for governance workflows. It runs locally with zero required dependencies and never makes network calls to Cogensec or any other service. It ships with extension points for custom threat detectors, custom policy rules, and custom validators.

**What it does not do.** The cortical layer's checks in v0.1.0 are pattern-based reference implementations — substring matching for prompt injection indicators, dictionary comparisons for action distribution drift, structural inspection of memory provenance. They are not LLM-backed semantic analysis. They will catch obvious cases and miss sophisticated paraphrased attacks. Production deployments should register custom detectors with domain-specific logic, and v0.2 will add LLM-backed cortical checks for users who want them out of the box. The library also does not currently include adapters for specific agent frameworks. It is framework-agnostic in the sense that it runs alongside any agent, but a developer using LangChain or the Claude Agent SDK still needs to write the plumbing to feed reasoning chains, memory reads, and tool outputs into the evaluator's context dict by hand. The first framework adapter is the next priority on the roadmap.

**What it deliberately is not.** It is not a guardrail. It does not block agent actions on its own — when an action is blocked, that is the result of explicit governance policy, not inferred risk. It is not a runtime enforcement layer trying to compete with WAF-style products. It is not a hosted service. It is a measurement and verification library, and everything it does is in service of producing evidence that an agent has (or lacks) the structural properties of a self-securing system.

---

## Quick Start

### Installation

```bash
pip install agentegrity
```

For cryptographic attestation signing, install the optional `crypto` extra:

```bash
pip install agentegrity[crypto]
```

### Basic Usage

```python
from agentegrity import AgentProfile, AgentType, DeploymentContext, RiskTier
from agentegrity import IntegrityEvaluator
from agentegrity.layers import AdversarialLayer, CorticalLayer, GovernanceLayer

# Define an agent profile
profile = AgentProfile(
    name="research-assistant",
    agent_type=AgentType.TOOL_USING,
    capabilities=["tool_use", "memory_access", "web_access"],
    deployment_context=DeploymentContext.CLOUD,
    risk_tier=RiskTier.MEDIUM,
)

# Initialize the evaluator with all three layers
evaluator = IntegrityEvaluator(
    layers=[
        AdversarialLayer(coherence_threshold=0.85),
        CorticalLayer(drift_tolerance=0.10),
        GovernanceLayer(policy_set="enterprise-default"),
    ]
)

# Evaluate agent integrity
result = evaluator.evaluate(profile, context={"action": {"type": "respond"}})
print(f"Composite score: {result.composite}")
print(f"Action: {result.action}")
print(f"Properties: {result.properties.to_dict()}")
```

### Runtime Monitoring with Attestation

```python
from agentegrity import IntegrityMonitor

monitor = IntegrityMonitor(
    profile=profile,
    evaluator=evaluator,
    threshold=0.70,
    enable_attestation=True,
)

@monitor.guard
async def agent_action(context=None):
    # Your agent logic here
    return await agent.execute(context)

# Each call runs pre-execution and post-execution integrity checks,
# appends a signed record to the attestation chain, and triggers
# violation handling if the score falls below threshold.
result = await agent_action(context={"action": {"type": "tool_call"}})

# Inspect the attestation chain
print(f"Records: {len(monitor.attestation_chain)}")
print(f"Chain valid: {monitor.attestation_chain.verify_chain()}")
```

See [`examples/`](examples/) for more complete walkthroughs including custom threat detectors and custom policy rules.

---

## Repository Structure

```
agentegrity-framework/
├── MANIFESTO.md                 # The Agentegrity Manifesto
├── README.md                    # You are here
├── LICENSE                      # Apache 2.0
├── pyproject.toml               # Package configuration
├── agentegrity-glossary.md      # Vocabulary of the discipline
│
├── spec/                        # Framework Specification
│   ├── SPECIFICATION.md         # Full technical specification
│   ├── properties/              # Property definitions
│   │   ├── adversarial-coherence.md
│   │   ├── environmental-portability.md
│   │   └── verifiable-assurance.md
│   └── layers/                  # Layer architecture
│       ├── adversarial-layer.md
│       ├── cortical-layer.md
│       └── governance-layer.md
│
├── src/agentegrity/             # Python Reference Implementation
│   ├── __init__.py
│   ├── core/                    # Core abstractions
│   │   ├── profile.py           # AgentProfile
│   │   ├── evaluator.py         # IntegrityEvaluator, PropertyWeights
│   │   ├── attestation.py       # AttestationRecord, AttestationChain
│   │   └── monitor.py           # IntegrityMonitor with @guard decorator
│   ├── layers/                  # Layer implementations
│   │   ├── adversarial.py       # AdversarialLayer (self-defense)
│   │   ├── cortical.py          # CorticalLayer (self-stability)
│   │   └── governance.py        # GovernanceLayer (policy + audit)
│   └── sdk/                     # High-level convenience wrapper
│       └── client.py            # AgentegrityClient
│
├── tests/                       # Test suite (45 tests, all passing)
│   ├── test_profile.py
│   ├── test_evaluator.py
│   ├── test_attestation.py
│   └── test_monitor.py
│
└── examples/                    # Usage examples
    ├── basic_evaluation.py
    ├── runtime_monitoring.py
    └── custom_validator.py
```

---

## Roadmap

**v0.1.0 — Reference implementation (current).** Three-layer architecture, pattern-based reference checks, cryptographic attestation, custom validator and policy extension points, 45 passing tests, three working examples. This is what's in this repository today.

**v0.2.0 — Claude Agent SDK and LLM-backed checks (next 4–6 weeks).** First framework adapter targeting the Claude Agent SDK with five integration points (Harness, Tools, Sandbox, Session, Orchestration). LLM-backed cortical checks using Claude for semantic analysis of reasoning chains and memory provenance. Self-recovery verification for the third self-securing capability. Async-first evaluator pipeline.

**v0.3.0 — Multi-framework and dashboard (next quarter).** Additional framework adapters for LangGraph, OpenAI Agents SDK, and CrewAI. Minimal web dashboard for first-run visualization. Hosted attestation registry as an optional commercial tier. Compliance report generation for EU AI Act, NIST AI RMF, and ISO 42001.

**v1.0.0 — Stable API (when ready).** Declared stable when the public API has been unchanged for a full minor release cycle, when the library has production deployments at three or more external organizations, and when the framework has been cited in at least one peer-reviewed publication. v1.0.0 is not a date — it's a signal that adoption has happened beyond our direct influence.

---

## Documentation

| Document | Description |
|---|---|
| [Manifesto](MANIFESTO.md) | The founding statement of agentegrity as a discipline |
| [Specification](spec/SPECIFICATION.md) | Full technical specification (properties, layers, controls, scoring) |
| [Glossary](agentegrity-glossary.md) | Vocabulary of the discipline, defined precisely |
| [Adversarial Layer](spec/layers/adversarial-layer.md) | Self-defense verification architecture |
| [Cortical Layer](spec/layers/cortical-layer.md) | Self-stability verification architecture |
| [Governance Layer](spec/layers/governance-layer.md) | Policy enforcement and audit architecture |

---

## Design Principles

1. **Self-securing capability is the goal. Verification is the methodology.** The framework exists because agents need to be able to secure themselves. The scoring system is how we prove they can. Without the underlying capability, the score is theater. Without the verification methodology, the capability is unprovable. Both are required.

2. **Composition layer, not model layer.** Better base models do not eliminate the need for agent-level verification. They make compositions more capable and therefore more dangerous when compromised. The framework is positioned at the composition layer specifically because that's the layer model improvements don't close.

3. **Defense-in-depth, not defense-in-replacement.** Guardrails, runtime monitors, and network controls remain essential. Agentegrity adds a layer that sits inside the agent's decision process where exogenous controls cannot reach. The two complement each other.

4. **Cryptographic, not observational.** "We monitored the agent and it looked fine" is not assurance. Attestation records produced by this library are signed, chained, and independently verifiable. Verification means you can prove what the agent's state was at a point in time, not just that someone watched it.

5. **Open standard, plural implementations.** The specification is open. The reference implementation is Apache 2.0. Other implementations are welcome from any vendor, any framework, any deployment context. The integrity of autonomous agents is too important to be proprietary, and a single-vendor standard isn't a standard.

6. **Honest about limitations.** Every claim the library makes is defensible in writing. When checks can't run, they say so. When the implementation is a pattern-based reference rather than semantic analysis, the README says so. The worst possible outcome for this project is a published benchmark showing that our claims are louder than our implementation. We avoid that outcome by being the first to name limitations.

---

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Priority areas for v0.2:
- Claude Agent SDK framework adapter
- LLM-backed cortical check implementations
- Self-recovery verification
- Async evaluator pipeline
- Compliance report generation

Priority areas for v0.3 and beyond:
- Additional framework adapters (LangGraph, OpenAI Agents SDK, CrewAI)
- Domain-specific validator libraries (healthcare, finance, embodied)
- Language ports (TypeScript, Go, Rust)
- Formal verification of layer interactions

---

## Citation

If you use the Agentegrity Framework in research or production, please cite:

```bibtex
@misc{agentegrity2026,
  title={The Agentegrity Framework: Building and Verifying Self-Securing Autonomous AI Agents},
  author={Cogensec Research},
  year={2026},
  url={https://github.com/requie/agentegrity-framework}
}
```

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

---

**Agentegrity is a Cogensec Research initiative.** The discipline is open. The framework is open. The code is open. We invite researchers, practitioners, and organizations building or deploying autonomous AI agents to adopt, implement, extend, and critique it.
