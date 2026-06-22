# Multi-Agent Extensions to the Four Properties

**Status:** Normative (introduced in v0.8)
**Version:** 0.1.0

---

## Purpose

This document is a normative addendum to the four property
specifications (Adversarial Coherence, Environmental Portability,
Verifiable Assurance, Recovery Integrity). It describes how each
property extends under **in-process multi-agent semantics** in
v0.8.

v0.8 does not introduce a fifth property. The architectural review
behind v0.8 concluded that the genuinely-new multi-agent signal
that doesn't already belong under one of the existing four
properties is narrow enough that it lives under
**Coordination Integrity** as a v0.9+ effort (with its own
`FederationLayer`). Until then, the multi-agent layer behavior is
expressed as extensions to AC / EP / VA / RI documented here.

This addendum is grounded in the v0.8 `AgentTopology` type
(`agentegrity.core.topology`), which is the immutable in-process
snapshot of the multi-agent system. Every layer reads it through
`context["topology_context"]`, populated by team-aware adapters at
instrument time.

---

## Vocabulary

The terms below are normative for the layer behaviors that
follow. Full definitions live in
[`agentegrity-glossary.md`](../../agentegrity-glossary.md).

- **Agent Topology**: an immutable snapshot of which agents
  participate, what roles they hold, and which communication
  channels exist between them.
- **Topology Kind**: one of `HUB_SPOKE`, `HIERARCHICAL_DAG`,
  `PEER_TO_PEER`, `GROUP_CHAT`.
- **Topology Snapshot**: a hashed point-in-time view; mutations
  produce a new snapshot with a new `content_hash`.
- **Peer Authority**: the property that a peer message or
  broadcast came from an agent declared in the receiving agent's
  topology.
- **Role Drift**: an agent's observed action distribution deviates
  from the baseline for its declared role.
- **Peer Quarantine**: a recovery capability allowing the system
  to isolate a compromised peer.
- **Subagent Orphan**: a `subagent_stop` event without a matching
  prior `subagent_start` (e.g., a dropped OTel span).

---

## Adversarial Coherence — multi-agent extensions

### Channel coverage

Single-agent AC scans `direct_prompt`, `memory_reads`,
`tool_responses`, `retrieved_documents`. Multi-agent AC adds the
three channels enabled by the `multi_agent_comm` capability:

- `peer_messages` (already wired in v0.7)
- `shared_memory` (new in v0.8)
- `broadcast_channels` (new in v0.8)

All three use the same regex taxonomy + LLM classifier the layer
already runs against single-agent channels, plus the new
`peer_coercion` family (three patterns: instruction-override,
direct-command, user-impersonation).

### Peer-authority check

A peer message or broadcast whose `sender_agent_id` is not in the
declared topology fires a `peer_authority` ThreatAssessment with
severity 0.70 and confidence 0.85.

If no topology is declared, the check is silent — single-agent
deployments and partially-instrumented multi-agent deployments do
not trigger false positives.

### Shared-memory attribution

Shared-memory writes carry `writer_agent_id` at ingest time. This
is normative: a SHA-256-equivalent attack signal MUST attribute to
the writer, not the reader. Implementations that observe shared
memory without identifying the writer SHOULD log a warning and
omit the entry rather than misattribute.

---

## Environmental Portability — multi-agent extensions

### Multi-agent emergence

The pre-v0.8 EP spec named "Multi-agent emergence — Integrity that
holds for a single agent but breaks in multi-agent deployments" as
an EP threat. v0.8 begins measuring it.

When `DeploymentContext.MULTI_AGENT` or `FEDERATED` is declared and
the topology context is populated, EP MUST account for the
emergence drift between the agent's single-agent baseline and the
observed multi-agent action distribution. v0.8 surfaces this via
the existing Cortical drift metric reading from the topology
context; an emergence-aware EP score is reserved for v0.9 (where
`FederationLayer` produces a richer topology_observed view that EP
can consume).

### Capability-context mismatch under multi-agent

Existing EP checks (an agent's declared capabilities matching its
deployment context) extend naturally to multi-agent: an agent
declaring `multi_agent_comm` but deployed without a topology, or
declaring a topology kind incompatible with its deployment context,
SHOULD reduce the EP score.

---

## Verifiable Assurance — multi-agent extensions

### Topology Evidence

Every attestation built when a topology is set MUST carry an
`Evidence(evidence_type="topology", source=topology_id,
content_hash=topology.content_hash())` entry. This cryptographically
commits the attestation to the structural shape the agent
participated in.

This Evidence is what makes verification of "the agent was in
*this* topology at the time of evaluation" possible without
expanding the canonical payload schema.

### Topology-change Evidence

When the topology mutates between two attestations, the second
attestation MUST carry an additional `Evidence(evidence_type=
"topology_change", source=previous_topology_id, content_hash=
new_topology.content_hash())` with the diff summarized in the
`summary` field.

A verifier walking a chain can reconstruct the topology lineage by
following the chain of `topology` and `topology_change` Evidence
without needing access to any external state.

### Cross-agent Evidence

Where a multi-agent system exchanges messages or handoffs, the
receiving agent's attestation MAY carry:

- `Evidence(evidence_type="peer_message", source=sender_agent_id,
  content_hash=SHA-256(message))` for peer messages.
- `Evidence(evidence_type="handoff", source=parent_agent_id,
  content_hash=parent_DecisionRecord.content_hash)` for handoffs.

`AttestationChain.verify_cross_agent_links(peer_chains)` validates
these point at real records in supplied peer chains with matching
`content_hash`. In v0.8 the validator is permissive when no peer
chains are supplied (returns True); v0.9 adds the `KeyProvider`
machinery that makes per-agent chains with cryptographic identity
the default for federated verification.

---

## Recovery Integrity — multi-agent extensions

### Cascade detection

Single-agent RI watches the agent's own composite score for
sustained degradation. Multi-agent RI extends this to a population
view by reading `peer_score_history` from the topology context.

A peer is "degrading" when its composite drops by more than
`degradation_threshold` over the configured `degradation_window`
with a downward trend (second-half min < first-half max minus
threshold). When **two or more peers** are degrading,
`cascade_compromise_suspected` is set in
`RecoveryAssessment.details`.

Cascade detection is signal-only; the recovery score itself does
not drop. The layer's `action` MUST escalate to `alert` when
cascade is suspected even if this agent's own metrics are fine —
the cascade is signal that compromise is propagating, and a human
should decide whether to quarantine.

### Peer-quarantine capability

`RECOVERY_CAPABILITIES` adds `"peer_quarantine"`. An agent profile
that declares this capability has `quarantine_capable=True` in its
`RecoveryAssessment`. This is informational in v0.8; v0.9's
`FederationLayer` can act on it (route around quarantined peers).

### Chain restore under multi-agent

Recovery's existing `chain_intact` check extends naturally: when
the chain holds topology Evidence, the restored chain MUST
preserve the topology Evidence content_hash through the restore
round-trip. The `Checkpoint` Protocol implementations already
preserve Evidence verbatim, so this requires no Recovery-layer
changes — it's a property of the restore pipeline.

---

## Governance

The pre-v0.8 `enterprise-default` policy set ships `GOV-004`:
*"Multi-agent coordination with >3 agents | Require approval"*.
Pre-v0.8 this rule was dead code — it gated on a synthetic
`action.type="multi_agent_coordination"` no adapter produced.

v0.8 makes the rule real. The rule now MUST read topology member
count from `topology_context.topology.members` when topology is
declared. When more than three members participate, the rule
fires `REQUIRE_APPROVAL`. The legacy action-based path is
preserved in parallel for callers that depend on it.

---

## Honest non-goals (v0.8)

The following are intentionally NOT covered by v0.8 and are
reserved for v0.9 or later:

- **A fifth property (Coordination Integrity)**. Reserved for
  v0.9. CI in v0.9 will narrowly cover topology coherence,
  message-graph conformance, and broadcast authority — the
  genuinely-new signal that doesn't already belong under
  AC/EP/VA/RI.
- **A fifth layer (FederationLayer)**. Reserved for v0.9. Owns
  the CI score and produces topology context for the other
  layers.
- **CorticalLayer role-conformance** (per-role baselines).
  Deferred — requires the `BaselineStore` to grow its key from
  `agent_id` to `(agent_id, role)` and a migration story for
  existing single-agent baselines.
- **`KeyProvider` Protocol + per-agent chains**. Reserved for
  v0.9. v0.8's `verify_cross_agent_links` is a permissive stub
  until per-agent identity is in place.
- **Cross-process / A2A federation**. Architecture is
  A2A-compatible (Evidence content-hash linking works across
  process boundaries); transport adoption is v1.0-candidate.
- **Fleet aggregator / Agentegrity Posture**. Reserved for v0.9.
  Will formalize the population-level concept the glossary
  already names.

---

## Mapping to threat model entries

See [`spec/threat-model.md`](../threat-model.md) for the
authoritative entries. The v0.8 multi-agent threats addressed by
the extensions in this document are:

| Threat | Property mitigation |
|---|---|
| T-CASCADE (compromise propagation via peer channels) | RI cascade detection + AC channel coverage extension |
| T-ROLE-DRIFT (agent in declared role acts outside it) | Deferred to v0.9 (Cortical per-role baselines) |
| T-SHARED-MEM-MISATTRIB (attack attributed to reader not writer) | AC shared-memory writer_agent_id attribution |
| T-ORPHAN-LIFECYCLE (sampling drops subagent_start) | Buffer-level orphan-stop event + warning log |
| T-BROADCAST-AMP (broadcast amplification of evaluation load) | Buffer-level per-broadcast rate limiting (1000/session cap) |
| T-TOPO-FALSE (forged topology declaration) | Reserved for v0.9 (KeyProvider + signed topology Evidence) |

---

## Implementation references

- `agentegrity.core.topology`: `AgentTopology`, `AgentMember`,
  `AgentRole`, `TopologyKind`, `TopologyChange`.
- `agentegrity.adapters.base._BaseAdapter.set_topology(...)`:
  the single entry point adapters call to declare topology.
- `agentegrity.adapters.base._BaseAdapter._handle_topology_*`,
  `_handle_peer_message`, `_handle_shared_memory_write`,
  `_handle_broadcast`, `_handle_task_started`: handler surface for
  the six new canonical events.
- `agentegrity.layers.adversarial.AdversarialLayer`: peer-coercion
  patterns + shared_memory/broadcast scanning + peer-authority check.
- `agentegrity.layers.recovery.RecoveryLayer._check_cascade`:
  cascade detection over `peer_score_history`.
- `agentegrity.layers.governance` `GOV-004` rule:
  `_rule_multi_agent_escalation` reads topology member count.
- `agentegrity.core.attestation.build_attestation_record(...,
  topology=..., topology_change=...)`: how topology Evidence
  attaches to attestations.
- `agentegrity.core.attestation.AttestationChain.verify_cross_agent_links`:
  v0.8 permissive stub; full implementation v0.9.
