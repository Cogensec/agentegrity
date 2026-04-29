# The Agentegrity Manifesto

**Version 1.0 · March 2026**
**Cogensec Research**

---

## A New Word for a New Problem

We coined *agentegrity* because the problem it names did not have a word.

Autonomous AI agents reason, decide, act, and coordinate — often without human oversight, often across environments their designers never anticipated. The security industry responded with guardrails: filters on inputs, monitors on outputs, policies on tool access. These are necessary. They are not sufficient.

An agent can pass every guardrail check and still be compromised. Its reasoning can be manipulated without triggering input filters. Its memory can be poisoned without producing anomalous outputs. Its behavior can drift from specification so gradually that no monitoring threshold fires. The agent looks fine. The agent is not fine.

**Agentegrity** is the property of an AI agent being *whole* — maintaining consistent, verifiable, trustworthy operation across adversarial conditions, deployment environments, and time.

It is not a product. It is not a checklist. It is a measurable, enforceable, provable property of the agent itself.

---

## Definition

> **Agentegrity** (noun): The condition in which an autonomous AI agent maintains coherent reasoning, consistent behavior, and verifiable trustworthiness under adversarial pressure, across deployment contexts, and over time.
>
> An agent possesses agentegrity when three properties hold simultaneously:
> **adversarial coherence**, **environmental portability**, and **verifiable assurance**.

---

## The Three Properties

### 1. Adversarial Coherence

An agent with adversarial coherence maintains consistent reasoning and decision-making under active attack. Its goals cannot be hijacked. Its reasoning chain cannot be silently redirected. Its outputs remain aligned with its specification even when its inputs, tools, memory, and peer agents are adversarially manipulated.

Adversarial coherence is not robustness in the ML sense — surviving perturbed inputs. It is *cognitive consistency* — the agent's internal reasoning process produces the same class of decisions regardless of adversarial interference in any channel: prompts, tool outputs, retrieved documents, inter-agent messages, or environmental signals.

**An agent without adversarial coherence can be turned against its operator without either party knowing.**

### 2. Environmental Portability

An agent with environmental portability carries its integrity guarantees across deployment contexts. The same agent deployed in a cloud sandbox, at the edge, within a multi-agent swarm, or controlling a physical system maintains equivalent integrity properties — not identical implementations, but equivalent assurances.

This matters because agents are increasingly deployed across heterogeneous environments. An agent that is secure in a controlled cloud environment but exploitable when federated with untrusted peers, or when deployed on constrained edge hardware, does not possess integrity — it possesses *conditional* integrity, which is no integrity at all.

**An agent without environmental portability is only as secure as its weakest deployment.**

### 3. Verifiable Assurance

An agent with verifiable assurance can *prove* its integrity state, not merely *claim* it. Integrity assessments produce cryptographic attestation records that are independently verifiable, tamper-evident, and auditable.

Observational security — "we watched the agent and it seemed fine" — is not assurance. Monitoring dashboards show what happened. Attestation records prove what the agent's state *was* at a specific point in time, signed in a way that cannot be retroactively altered.

**An agent without verifiable assurance asks you to trust it. An agent with verifiable assurance lets you verify it.**

---

## The Four Layers

Agentegrity is enforced through four architectural layers. Each layer addresses a different dimension of integrity. Together, they form a complete integrity envelope around the agent.

### Adversarial Layer

The adversarial layer is the outermost defense. It continuously tests and validates the agent's resilience to attack. This is not a one-time penetration test — it is an ongoing, runtime evaluation of how the agent responds to adversarial pressure across all input channels.

Core functions:
- Attack surface enumeration and continuous mapping
- Real-time threat detection across prompt, tool, memory, and peer channels
- Adversarial coherence scoring against behavioral baselines
- Red team validation hooks for automated and manual testing
- Threat intelligence integration for emerging attack patterns

The adversarial layer answers: **can this agent be broken right now?**

### Cortical Layer

The cortical layer monitors the agent's internal cognitive processes — the reasoning, memory, and decision-making that define what the agent *is*. Named for the cerebral cortex, the brain's executive processing center, this layer protects the higher-order functions that make an agent intelligent and make it dangerous when compromised.

Core functions:
- Reasoning chain integrity validation
- Memory provenance tracking and consistency verification
- Behavioral baseline maintenance and drift detection
- Cognitive conflict detection (contradictions between goals, instructions, memory, and actions)
- Internal state attestation and signing

The cortical layer answers: **is this agent still itself?**

### Governance Layer

The governance layer enforces organizational policy, human oversight requirements, and compliance obligations. It is the bridge between the agent's technical integrity and the organizational context in which it operates.

Core functions:
- Policy-as-code enforcement with version control
- Human-in-the-loop escalation for high-risk decisions
- Compliance mapping to regulatory frameworks (NIST AI RMF, EU AI Act, ISO 42001)
- Immutable audit trail generation
- Break-glass emergency controls

The governance layer answers: **is this agent operating within authorized boundaries?**

### Recovery Layer

The recovery layer verifies the agent's self-recovery capability. Where the adversarial layer keeps attacks out and the cortical layer detects degradation while it happens, the recovery layer is what runs when both have been bypassed: it tracks the trajectory of the agent's integrity over time, verifies the audit trail is intact, and confirms the agent declares the mechanisms it would need to roll back to a known-good state.

Core functions:
- Sustained-degradation detection across a rolling window of composite scores
- Behavioral baseline freshness and continuity checks
- Recovery capability assessment against the agent's declared profile
- Attestation chain continuity verification (tamper-evident audit trail)
- Escalation signals when continuity is broken

The recovery layer answers: **if this agent has been compromised, can it restore itself?**

---

## What Agentegrity Is Not

**Agentegrity is not guardrails.** Guardrails filter. Agentegrity evaluates the agent itself.

**Agentegrity is not monitoring.** Monitoring observes behavior. Agentegrity proves integrity state.

**Agentegrity is not alignment.** Alignment asks whether the agent's goals are good. Agentegrity asks whether the agent's goals are *intact*.

**Agentegrity is not a product.** It is a measurable property. Products can implement it. Frameworks can enforce it. Benchmarks can evaluate it. But no single vendor owns agentegrity any more than a single vendor owns "encryption."

---

## The Standard

This manifesto defines the concept. The [Agentegrity Framework Specification](spec/SPECIFICATION.md) defines the technical standard — how to measure the three properties, how to implement the four layers, what controls are required at each maturity level, and how to produce verifiable attestation records.

The specification is open. Implementations are welcome from any vendor, any framework, any deployment context. The integrity of autonomous agents is too important to be proprietary.

---

## Signatories

**Cogensec Research** — Originator and primary maintainer

We invite researchers, practitioners, and organizations building or deploying autonomous AI agents to adopt, implement, and extend the Agentegrity Framework.

---

*Agentegrity is a coined term introduced by Cogensec Research in March 2026.*
