# The Agentegrity Glossary

**A living vocabulary for the discipline of AI agent structural integrity.**
Version 1.0 · March 2026 · Cogensec

---

> This glossary defines the core terminology of agentegrity — the discipline of securing autonomous AI agents through intrinsic structural integrity rather than exogenous constraints. These terms are intended for use by researchers, engineers, security practitioners, and standards bodies working at the intersection of AI agent systems, adversarial security, and physical AI.
>
> Terms marked with † are novel to the agentegrity discipline. All others are existing concepts recontextualized within the agentegrity framework.

---

## Core Concepts

**Agentegrity** †
The structural integrity of an autonomous AI agent — its measurable capacity to maintain intended behavior, decision coherence, and operational safety under adversarial conditions, across any environment it operates in. Agentegrity is a property of the agent's architecture, not an external layer applied to it. Analogous to structural integrity in engineering: a bridge with integrity bears its load without external support. An agent with agentegrity maintains its security without external guardrails.

**Agentegrity Score** †
A composite metric quantifying an agent's structural integrity across four dimensions: adversarial resistance, behavioral consistency, recovery integrity, and cross-domain portability. Expressed as a normalized value enabling comparison across agent architectures, deployment environments, and operational contexts. The agentegrity score is the primary output of a formal agentegrity assessment.

**Agentegrity Assessment** †
A structured evaluation of an AI agent's structural integrity using the Agentegrity Framework methodology. Combines automated adversarial red teaming, behavioral benchmarking, recovery testing, and cross-domain portability validation to produce an agentegrity score. Distinguished from a penetration test by its focus on the agent's intrinsic resilience rather than its perimeter defenses.

**Agentegrity Posture** †
The aggregate agentegrity state of an organization's deployed AI agent population at a given point in time. Encompasses individual agent scores, environmental coverage, policy compliance, and degradation trends. Analogous to "security posture" in traditional cybersecurity, but specific to autonomous agent systems.

**Exogenous Security**
Security measures applied to an AI agent from outside its decision architecture. Includes guardrails, input-output filters, policy wrappers, and inference-time safety layers. Exogenous security does not alter the agent's internal reasoning and does not persist when external controls are removed or bypassed. Contrast with *intrinsic security*.

**Intrinsic Security** †
Security properties embedded within an AI agent's own model architecture, reasoning chain, and behavioral framework. Intrinsic security operates inside the agent's decision loop, travels with the agent across environments, and persists without external enforcement. The foundational principle of the agentegrity discipline.

**Guardrail Dependency** †
The condition in which an AI agent's security relies entirely on exogenous controls, leaving no residual defense when those controls are removed, bypassed, or misconfigured. Guardrail dependency is the primary failure mode that agentegrity is designed to eliminate. An agent with guardrail dependency has an agentegrity score approaching zero regardless of how sophisticated its external protections are.

---

## Threat Model

**Perception-Decision-Action Loop (PDA Loop)**
The complete information flow within an autonomous AI agent: sensory or data inputs (perception), model-based reasoning and planning (decision), and tool invocation or physical actuation (action). The PDA loop is the primary attack surface in the agentegrity framework. Each stage presents distinct vulnerability classes.

**Adversarial Coherence** †
The property of an AI agent whose decision-making remains consistent and aligned with its intended purpose under adversarial perturbation. An agent with high adversarial coherence does not exhibit goal drift, policy violation, or behavioral divergence when subjected to prompt injection, tool manipulation, memory poisoning, or sensory spoofing.

**Behavioral Drift** †
The gradual, often imperceptible deviation of an AI agent's actions from its intended behavioral policy over extended operational periods. Behavioral drift can be induced adversarially (through slow poisoning of memory or context) or emerge organically (through compounding model uncertainty across decision chains). One of the most insidious failure modes in autonomous systems because it evades point-in-time testing.

**Behavioral Drift Rate (BDR)** †
A quantitative measure of how rapidly an agent's observed behavior diverges from its intended policy baseline per unit of operational time or decision cycles. Expressed as a percentage deviation per time interval. A core metric in the behavioral consistency dimension of the agentegrity score.

**Cascade Compromise** †
A failure mode in multi-agent systems where the compromise of one agent propagates through tool calls, shared memory, or inter-agent communication to corrupt downstream agents. Cascade compromise is uniquely dangerous in agentic architectures because it exploits trust relationships between agents. Agentegrity at the individual agent level is necessary but insufficient to prevent cascade compromise — system-level agentegrity assessment is also required.

**Prompt-to-Physical Exploit** †
An attack vector in which adversarial input delivered through a digital channel (prompt injection, tool output poisoning, memory corruption) results in unintended physical action by an embodied AI agent. Represents the convergence of digital and physical AI security threat models. Example: a poisoned data source causes a warehouse robot to exceed safe operating boundaries.

**Actuation Hijacking** †
The adversarial manipulation of an embodied AI agent's physical actuators — robotic arms, vehicle controls, drone flight systems, industrial machinery — through compromise of the agent's decision architecture rather than direct hardware exploitation. Distinguished from traditional OT attacks by targeting the AI reasoning layer rather than the control system itself.

**Sensor Spoofing (Agentic Context)**
The injection of falsified sensory data — visual, lidar, radar, acoustic, or proprioceptive — into an embodied AI agent's perception layer to induce incorrect decisions. In the agentegrity framework, sensor spoofing is assessed as a perception-layer attack on the PDA loop. An agent with high adversarial resistance detects sensory anomalies before they influence the decision layer.

**Sim-to-Real Transfer Attack** †
An adversarial technique that exploits the gap between simulated training environments and real-world deployment conditions to induce agent failure. Attackers craft inputs that are benign in simulation but adversarial in physical deployment, exploiting the domain gap that AI agents trained in synthetic environments inherently possess.

**Memory Poisoning (Persistent)**
The corruption of an AI agent's long-term memory or context store such that adversarial beliefs, false policies, or manipulated history persist across sessions and influence future decisions. Distinguished from single-session prompt injection by its durability. An agent with high recovery integrity can detect and remediate persistent memory corruption.

**Confused Deputy (Agentic Context)**
A privilege escalation attack in which an adversary does not directly access a target system but instead manipulates a trusted AI agent into performing unauthorized actions on its behalf. Exploits the agent's legitimate tool access and permissions. The agentic confused deputy is particularly dangerous because the agent believes it is acting correctly.

---

## Measurement

**Adversarial Resistance Index (ARI)** †
A composite score measuring an agent's resilience to adversarial attack across standardized test suites. Covers prompt injection resistance, tool misuse detection, memory integrity under poisoning, behavioral manipulation resistance, and — for physical agents — sensor spoofing resilience and actuation boundary enforcement. The primary metric in the adversarial resistance dimension of the agentegrity score.

**Behavioral Consistency Index (BCI)** †
A measure of an agent's decision-making stability across environmental variations, input perturbations, and extended operational periods. Computed by comparing the agent's decision distribution under normal and perturbed conditions. High BCI indicates the agent's behavior is robust. Low BCI indicates susceptibility to adversarial or environmental drift.

**Recovery Half-Life** †
The time (or number of decision cycles) required for a compromised agent to restore 50% of its intended behavioral baseline following a successful attack. A core metric in the recovery integrity dimension of the agentegrity score. An agent with a short recovery half-life returns to intended operation quickly. An agent with a long recovery half-life remains in a degraded or compromised state, extending the attacker's window of exploitation.

**Cross-Domain Portability Score (CDPS)** †
A measure of how well an agent's security properties transfer across operating environments — from sandbox to production, from cloud to edge, from digital to physical. Computed by measuring agentegrity score variance across controlled environment transitions. An agent with high CDPS maintains consistent security regardless of deployment context. An agent with low CDPS is environment-dependent and requires per-deployment security engineering.

**Agentegrity Degradation Curve** †
A time-series representation of an agent's agentegrity score over its operational lifecycle. Tracks how structural integrity changes as the agent accumulates operational history, context, memory, and environmental exposure. Used to identify degradation patterns, predict maintenance windows, and trigger remediation before agentegrity drops below acceptable thresholds.

**Cortical Embedding Depth** †
A measure of how deeply security intelligence is integrated into an agent's decision architecture, ranging from surface-level (output filtering) to deep (embedded within the reasoning chain). Higher cortical embedding depth correlates with stronger intrinsic security. An agent with shallow embedding relies on interception; an agent with deep embedding reasons about security natively.

---

## Architecture

**Agentegrity Stack** †
The four-layer security architecture required to build and maintain agentegrity in autonomous AI agents: the Adversarial Layer (continuous red teaming), the Cortical Layer (embedded security models), the Governance Layer (runtime monitoring and compliance), and the Recovery Layer (compromise detection and continuity verification). The four layers form a closed loop: attack, defend, observe, recover, repeat.

**Adversarial Layer** †
The first layer of the agentegrity stack. Responsible for continuously testing the agent's defenses through automated adversarial red teaming, attack simulation, and vulnerability probing across the full PDA loop. In physical AI systems, includes simulation-based adversarial testing in synthetic environments. The adversarial layer does not wait for attacks — it manufactures them proactively.

**Cortical Layer** †
The second layer of the agentegrity stack. A family of specialized security models embedded within the agent's decision architecture. Cortical models perform real-time adversarial input detection, policy enforcement, behavioral anomaly detection, and decision validation. They operate inside the agent's reasoning loop, not outside it. Named for their functional role as the agent's cognitive security cortex.

**Governance Layer** †
The third layer of the agentegrity stack. Provides runtime monitoring, observability, and compliance enforcement across deployed agent populations. Tracks agentegrity scores over time, detects degradation, manages security policy updates, and produces audit-ready assurance evidence. Operates at the fleet level rather than the individual agent level.

**Recovery Layer** †
The fourth layer of the agentegrity stack. Verifies the agent's self-recovery capability — its ability to detect when integrity has been compromised and restore itself to a known-good state. Watches the rolling history of composite scores for sustained degradation, verifies attestation chain continuity, checks behavioral baseline freshness, and confirms the agent declares the recovery mechanisms (`state_restore`, `checkpoint`, `rollback`, `session_reset`) it would need to roll back. Where the adversarial layer keeps attacks out and the cortical layer detects degradation while it happens, the recovery layer is what runs when both have been bypassed.

**Cortical Model** †
A purpose-built security model designed for embedding within an AI agent's decision architecture. Unlike guardrail models that operate at the input-output boundary, cortical models participate in the agent's reasoning process — detecting adversarial patterns, enforcing behavioral policies, and validating decision coherence in real time. The Cortex Series is an implementation of the cortical model concept.

**Security Reflex** †
An automated, low-latency defensive response triggered by a cortical model when adversarial conditions are detected. Analogous to a biological reflex: it executes before conscious reasoning, preventing adversarial inputs from reaching the decision layer. Security reflexes are the fastest layer of intrinsic defense. Example: immediate rejection of an input that matches known injection patterns before the primary model processes it.

---

## Domain-Specific Concepts

**Digital Agentegrity** †
The application of agentegrity principles to AI agents operating in software environments — orchestrating APIs, invoking tools, managing data, interacting with cloud services, and communicating with other digital agents. Digital agentegrity addresses threat models including prompt injection, tool misuse, MCP protocol exploitation, memory poisoning, and multi-agent cascade compromise.

**Physical Agentegrity** †
The application of agentegrity principles to AI agents operating in the physical world — robotic systems, autonomous vehicles, drones, industrial machinery, and smart infrastructure. Physical agentegrity addresses threat models including sensor spoofing, actuation hijacking, sim-to-real transfer attacks, and adversarial manipulation of physical environments. Encompasses safety-critical considerations where agent failure results in real-world harm.

**Convergent Agentegrity** †
The unified practice of securing AI agents that operate fluidly across digital and physical domains. Based on the thesis that the digital-physical security boundary is dissolving as agents increasingly coordinate software systems and physical actuators simultaneously. Convergent agentegrity is the terminal state of the discipline — the point at which digital and physical agentegrity are no longer distinguishable specializations but aspects of a single practice.

**Environmental Portability** †
The property of security measures that persist when an AI agent transitions between operating environments. In the agentegrity framework, environmental portability is a defining characteristic of intrinsic security (which travels with the agent) versus exogenous security (which must be rebuilt per environment). A core pillar of the agentegrity thesis: security that depends on the environment is not agentegrity.

**Agent Recovery State** †
The behavioral condition of an AI agent during the period between successful compromise and full restoration of intended operation. During the agent recovery state, the agent may exhibit degraded decision quality, inconsistent behavior, or partial policy compliance. The duration and severity of the agent recovery state are key determinants of the recovery integrity dimension of the agentegrity score.

---

## Organizational Concepts

**Agentegrity Policy** †
An organizational directive specifying minimum acceptable agentegrity scores, assessment frequency, remediation timelines, and reporting requirements for deployed AI agent populations. Functions as the agent-specific equivalent of a security policy. Defines which agentegrity dimensions are weighted most heavily based on organizational risk profile.

**Agentegrity Certification** †
A formal attestation that an AI agent or agent system meets a defined agentegrity threshold across all four measurement dimensions. Issued on the basis of a completed agentegrity assessment. Analogous to SOC 2 or ISO 27001 certification but specific to autonomous agent systems. Anticipated to become a regulatory requirement as AI agent deployment scales.

**Agentegrity Debt** †
The accumulated deficit between an organization's current agentegrity posture and its defined agentegrity policy requirements. Analogous to technical debt: agentegrity debt increases when agents are deployed without assessment, when assessment findings are not remediated, or when agents operate beyond their assessed environments. Agentegrity debt compounds — unaddressed vulnerabilities degrade overall posture and increase cascade compromise risk.

---

*This glossary is maintained by Cogensec as a public resource for the agentegrity discipline. Terms, definitions, and measurement specifications will evolve as the field matures. Contributions, critiques, and extensions are welcome.*

*Cogensec · AI Security Research Lab · [cogensec.com](https://cogensec.com)*
