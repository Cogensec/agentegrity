"""
Governance Layer - enforces organizational policy, human oversight,
and compliance requirements.

The governance layer is the bridge between the agent's technical integrity
and the organizational context in which it operates. It is the innermost
layer, closest to action execution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from agentegrity.core.evaluator import LayerResult
from agentegrity.core.profile import AgentProfile, RiskTier


class PolicyDecision(str, Enum):
    """Result of a policy evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    CONDITIONAL = "conditional"


@dataclass
class PolicyRule:
    """
    A single policy rule in the governance layer.

    Parameters
    ----------
    rule_id : str
        Unique identifier for the rule.
    name : str
        Human-readable name.
    description : str
        What this rule enforces.
    condition : callable
        Function that takes (profile, action, context) and returns bool.
        True means the rule is triggered (violation detected).
    decision : PolicyDecision
        What to do when the rule triggers.
    severity : float
        How severely this rule impacts the governance score (0.0 - 1.0).
    """

    rule_id: str
    name: str
    description: str
    condition: Callable[[AgentProfile, dict[str, Any], dict[str, Any]], bool]
    decision: PolicyDecision = PolicyDecision.DENY
    severity: float = 0.50

    def evaluate(
        self,
        profile: AgentProfile,
        action: dict[str, Any],
        context: dict[str, Any],
    ) -> PolicyEvaluation:
        try:
            triggered = self.condition(profile, action, context)
        except Exception as e:
            # Rule evaluation failure is treated as a soft trigger
            return PolicyEvaluation(
                rule_id=self.rule_id,
                triggered=True,
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason=f"Rule evaluation error: {str(e)}",
            )

        return PolicyEvaluation(
            rule_id=self.rule_id,
            triggered=triggered,
            decision=self.decision if triggered else PolicyDecision.ALLOW,
            reason=self.description if triggered else None,
        )


@dataclass
class PolicyEvaluation:
    """Result of evaluating a single policy rule."""

    rule_id: str
    triggered: bool
    decision: PolicyDecision
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "triggered": self.triggered,
            "decision": self.decision.value,
            "reason": self.reason,
        }


@dataclass
class AuditEntry:
    """An immutable audit log entry."""

    entry_id: str
    agent_id: str
    timestamp: datetime
    action: str
    policy_evaluations: list[PolicyEvaluation]
    decision: str  # final decision
    content_hash: str  # hash of the entry for tamper detection

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "policy_evaluations": [pe.to_dict() for pe in self.policy_evaluations],
            "decision": self.decision,
            "content_hash": self.content_hash,
        }


# Built-in policy rules
def _rule_high_risk_tool_access(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Block high-risk agents from using sensitive tools without approval."""
    sensitive_tools = {"database_write", "file_delete", "payment_execute", "admin_api"}
    action_tool = action.get("tool", "")
    return (
        profile.risk_tier in (RiskTier.HIGH, RiskTier.CRITICAL)
        and action_tool in sensitive_tools
    )


def _rule_code_execution_boundary(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Require approval for code execution actions."""
    return action.get("type") == "code_execution" and not context.get("sandbox", False)


def _rule_financial_threshold(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Flag financial transactions above threshold."""
    amount = action.get("amount", 0)
    threshold = context.get("financial_threshold", 1000)
    return action.get("type") == "financial" and amount > threshold


def _rule_multi_agent_escalation(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Require human oversight for multi-agent coordination actions."""
    return (
        action.get("type") == "multi_agent_coordination"
        and action.get("agent_count", 0) > 3
    )


# Default policy sets
DEFAULT_POLICIES: dict[str, list[PolicyRule]] = {
    "enterprise-default": [
        PolicyRule(
            rule_id="GOV-001",
            name="High-Risk Tool Access",
            description="High/critical risk agents require approval for sensitive tool use",
            condition=_rule_high_risk_tool_access,
            decision=PolicyDecision.REQUIRE_APPROVAL,
            severity=0.70,
        ),
        PolicyRule(
            rule_id="GOV-002",
            name="Code Execution Boundary",
            description="Code execution outside sandbox requires approval",
            condition=_rule_code_execution_boundary,
            decision=PolicyDecision.REQUIRE_APPROVAL,
            severity=0.60,
        ),
        PolicyRule(
            rule_id="GOV-003",
            name="Financial Threshold",
            description="Financial transactions above threshold require approval",
            condition=_rule_financial_threshold,
            decision=PolicyDecision.REQUIRE_APPROVAL,
            severity=0.80,
        ),
        PolicyRule(
            rule_id="GOV-004",
            name="Multi-Agent Escalation",
            description="Multi-agent coordination with >3 agents requires human oversight",
            condition=_rule_multi_agent_escalation,
            decision=PolicyDecision.REQUIRE_APPROVAL,
            severity=0.50,
        ),
    ],
    "minimal": [],
    "strict": [
        PolicyRule(
            rule_id="GOV-STRICT-001",
            name="All Tool Access",
            description="All tool access requires approval in strict mode",
            condition=lambda p, a, c: a.get("type") == "tool_call",
            decision=PolicyDecision.REQUIRE_APPROVAL,
            severity=0.50,
        ),
    ],
}


class GovernanceLayer:
    """
    The governance layer enforces organizational policy and compliance.

    Parameters
    ----------
    policy_set : str
        Name of a built-in policy set ("enterprise-default", "minimal", "strict")
        or "custom" if providing custom rules.
    custom_rules : list[PolicyRule], optional
        Custom policy rules. Added to the policy set rules.
    enable_audit : bool
        Whether to generate audit log entries. Default True.
    escalation_callback : callable, optional
        Function called when a policy requires human approval.
        Receives (AgentProfile, action, PolicyEvaluation).
    """

    def __init__(
        self,
        policy_set: str = "enterprise-default",
        custom_rules: list[PolicyRule] | None = None,
        enable_audit: bool = True,
        escalation_callback: Callable[..., Any] | None = None,
    ):
        self.policy_set_name = policy_set
        self._rules = list(DEFAULT_POLICIES.get(policy_set, []))
        if custom_rules:
            self._rules.extend(custom_rules)
        self.enable_audit = enable_audit
        self.escalation_callback = escalation_callback
        self._audit_log: list[AuditEntry] = []

    @property
    def name(self) -> str:
        return "governance"

    def evaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        """
        Evaluate the agent's action against governance policies.
        """
        ctx = context or {}
        action = ctx.get("action", {})
        evaluations: list[PolicyEvaluation] = []

        for rule in self._rules:
            evaluation = rule.evaluate(profile, action, ctx)
            evaluations.append(evaluation)

        # Determine overall decision
        triggered = [e for e in evaluations if e.triggered]
        denied = [e for e in triggered if e.decision == PolicyDecision.DENY]
        approvals = [e for e in triggered if e.decision == PolicyDecision.REQUIRE_APPROVAL]

        if denied:
            final_action = "block"
            passed = False
        elif approvals:
            final_action = "escalate"
            passed = False
            # Call escalation callback
            if self.escalation_callback:
                for approval in approvals:
                    self.escalation_callback(profile, action, approval)
        elif triggered:
            final_action = "alert"
            passed = True  # Conditional triggers don't fail
        else:
            final_action = "pass"
            passed = True

        # Compute governance score
        if not self._rules:
            score = 1.0
        else:
            violation_impact = sum(
                r.severity
                for r in self._rules
                for e in evaluations
                if e.rule_id == r.rule_id and e.triggered
            )
            max_impact = sum(r.severity for r in self._rules)
            score = max(0.0, 1.0 - (violation_impact / max_impact if max_impact > 0 else 0))

        score = round(score, 4)

        # Audit log
        if self.enable_audit:
            self._write_audit(profile, action, evaluations, final_action)

        return LayerResult(
            layer_name=self.name,
            score=score,
            passed=passed,
            action=final_action,
            details={
                "assurance_score": score,  # Maps to verifiable assurance
                "policy_set": self.policy_set_name,
                "rules_evaluated": len(self._rules),
                "rules_triggered": len(triggered),
                "evaluations": [e.to_dict() for e in evaluations],
                "audit_enabled": self.enable_audit,
            },
        )

    def _write_audit(
        self,
        profile: AgentProfile,
        action: dict[str, Any],
        evaluations: list[PolicyEvaluation],
        decision: str,
    ) -> None:
        """Write an immutable audit log entry."""
        entry_data = {
            "agent_id": profile.agent_id,
            "action": action,
            "evaluations": [e.to_dict() for e in evaluations],
            "decision": decision,
        }
        content_hash = hashlib.sha256(
            json.dumps(entry_data, sort_keys=True).encode()
        ).hexdigest()

        entry = AuditEntry(
            entry_id=f"audit-{len(self._audit_log) + 1}",
            agent_id=profile.agent_id,
            timestamp=datetime.now(timezone.utc),
            action=json.dumps(action),
            policy_evaluations=evaluations,
            decision=decision,
            content_hash=content_hash,
        )
        self._audit_log.append(entry)

    @property
    def audit_log(self) -> list[AuditEntry]:
        return list(self._audit_log)

    @property
    def rules(self) -> list[PolicyRule]:
        return list(self._rules)

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a custom policy rule."""
        self._rules.append(rule)

    def emergency_stop(self, agent_id: str, reason: str) -> dict[str, Any]:
        """
        Emergency break-glass control. Immediately flags an agent
        for suspension.
        """
        return {
            "action": "emergency_stop",
            "agent_id": agent_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "requires": "manual_review_before_restart",
        }

    def __repr__(self) -> str:
        return (
            f"GovernanceLayer(policy_set={self.policy_set_name!r}, "
            f"rules={len(self._rules)}, audit={'on' if self.enable_audit else 'off'})"
        )
