# Governance Layer

**Status:** Normative
**Version:** 1.0.0

---

## Purpose

The governance layer enforces organizational policy, human oversight requirements, and compliance obligations. It is the bridge between the agent's technical integrity and the organizational context in which it operates. It answers one question: **is this agent operating within authorized boundaries?**

## Position in Architecture

```
┌─── Adversarial Layer ───┐
│                          │
├─── Cortical Layer ───────┤
│                          │
├─── Governance Layer ─────┤  ← You are here (innermost, closest to action)
│                          │
└──────────────────────────┘
```

The governance layer evaluates last, after adversarial and cortical checks pass. It makes the final authorization decision before the agent's action executes.

## Components

### Policy Engine

Evaluates agent actions against a set of policy-as-code rules. Policies are versioned, auditable, and composable.

**Inputs:** AgentProfile, action, context
**Outputs:** PolicyDecision (allow, deny, require_approval, conditional)
**Latency target:** < 5ms p99

### Escalation Manager

Routes high-risk decisions to human oversight. Manages approval workflows and tracks pending escalations.

**Inputs:** Action, risk assessment, policy evaluation
**Outputs:** EscalationResult
**Latency target:** < 100ms p99

### Compliance Mapper

Maps integrity evaluations to regulatory and standards frameworks. Produces compliance evidence for audit.

**Inputs:** Integrity evaluation results
**Outputs:** Compliance mapping records
**Frequency:** Async (on evaluation completion)

Supported framework mappings:
- NIST AI RMF
- EU AI Act
- ISO 42001
- SOC 2 Type II
- OWASP Top 10 for LLMs

### Audit Logger

Produces immutable, hash-linked audit records for every policy evaluation. Each entry includes the action, all policy evaluations, the final decision, and a content hash for tamper detection.

**Inputs:** Policy evaluation results
**Outputs:** AuditEntry
**Latency target:** < 2ms p99

### Break-Glass Controller

Emergency override control that immediately suspends agent operation. Intended for critical incidents where normal policy evaluation is insufficient.

**Inputs:** Agent ID, reason
**Outputs:** StopConfirmation (requires manual review before restart)
**Latency target:** < 10ms p99

## Required Interfaces

```python
class GovernanceLayer(Protocol):
    def evaluate(self, profile: AgentProfile, context: dict) -> LayerResult: ...
    def enforce_policy(self, action: dict, policy_set: str) -> PolicyDecision: ...
    def escalate(self, action: dict, risk: dict) -> EscalationResult: ...
    def emergency_stop(self, agent_id: str, reason: str) -> StopConfirmation: ...
```

## Built-in Policy Sets

### `enterprise-default`

| Rule ID | Name | Trigger | Decision |
|---|---|---|---|
| GOV-001 | High-Risk Tool Access | High/critical risk agents accessing sensitive tools | Require approval |
| GOV-002 | Code Execution Boundary | Code execution outside sandbox | Require approval |
| GOV-003 | Financial Threshold | Financial transactions above configurable threshold | Require approval |
| GOV-004 | Multi-Agent Escalation | Multi-agent coordination with >3 agents | Require approval |

### `minimal`

No rules. All actions pass. Suitable for development and testing only.

### `strict`

All tool access requires human approval. Suitable for initial deployment of high-risk agents.

## Custom Policy Rules

Organizations define custom rules using the `PolicyRule` interface:

```python
from agentegrity.layers.governance import PolicyRule, PolicyDecision

rule = PolicyRule(
    rule_id="CUSTOM-001",
    name="PII Access Control",
    description="Block agents from accessing PII without classification clearance",
    condition=lambda profile, action, ctx: (
        action.get("data_classification") == "pii"
        and "pii_clearance" not in profile.capabilities
    ),
    decision=PolicyDecision.DENY,
    severity=0.90,
)

governance = GovernanceLayer(policy_set="enterprise-default", custom_rules=[rule])
```

## Governance Scoring

The governance score is computed as:

```
score = 1.0 - (sum of triggered rule severities / sum of all rule severities)
```

A score of 1.0 means no policies were violated. A score of 0.0 means every policy was violated.

## Action Determination

| Condition | Action |
|---|---|
| No rules triggered | `pass` |
| Rules triggered with `CONDITIONAL` decision | `alert` (pass continues) |
| Rules triggered with `REQUIRE_APPROVAL` decision | `escalate` |
| Rules triggered with `DENY` decision | `block` |

Under `enforce=True`, both `escalate` and `block` deny the action. A
`block` always denies; an `escalate` denies UNLESS an `approval_handler`
(configured on the adapter or monitor) approves it. Without enforcement,
both are advisory. This means the built-in `REQUIRE_APPROVAL` policies
(high-risk tool access, code-execution boundary, financial threshold,
multi-agent escalation) actually gate actions under enforcement rather
than passing through.

## Audit Trail

Every governance evaluation produces an audit entry regardless of outcome. The audit trail is the primary compliance evidence for organizational and regulatory audits.

Audit entries are hash-linked — each entry includes a content hash that can be verified for tamper detection. This is distinct from the attestation chain (which covers all three layers); the governance audit trail is a governance-specific record.
