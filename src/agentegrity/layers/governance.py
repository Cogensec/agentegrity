"""
Governance Layer - enforces organizational policy, human oversight,
and compliance requirements.

The governance layer is the bridge between the agent's technical integrity
and the organizational context in which it operates. It is the innermost
layer, closest to action execution.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Collection

from agentegrity.core.evaluator import LayerResult
from agentegrity.core.profile import AgentProfile, RiskTier
from agentegrity.core.tool_classifier import ToolCallCategory

logger = logging.getLogger("agentegrity.governance")


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
            # Rule evaluation failure is treated as a soft trigger. The
            # reason is serialized into the audit log and the attestation
            # layer_states, so it carries only the exception CLASS, not
            # the raw message (which can embed secrets/PII from whatever
            # the custom condition touched). Full detail goes to the
            # local log for operators.
            logger.warning(
                "governance rule %s evaluation failed: %s",
                self.rule_id, e, exc_info=True,
            )
            return PolicyEvaluation(
                rule_id=self.rule_id,
                triggered=True,
                decision=PolicyDecision.REQUIRE_APPROVAL,
                reason=f"Rule evaluation error: {type(e).__name__}",
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
# Default set of tool names treated as sensitive by GOV-001. This is a
# starting set, NOT an exhaustive one — operators should enumerate their
# deployment's sensitive tools via GovernanceLayer(sensitive_tools=...)
# or per-call context["sensitive_tools"]. Matching (see
# tool_name_matches):
#   * exact string, as always;
#   * glob patterns — an entry containing "*" or "?" is matched with
#     fnmatch, so "mcp__db__*" gates a whole MCP server;
#   * MCP suffix — "mcp__<server>__<tool>" also matches an entry naming
#     the bare <tool>, since the prefix is transport namespacing, not a
#     different tool ("file_delete" gates "mcp__filesystem__file_delete").
# Anything that matches none of these is not gated.
DEFAULT_SENSITIVE_TOOLS: frozenset[str] = frozenset(
    {"database_write", "file_delete", "payment_execute", "admin_api"}
)


def tool_name_matches(tool: str, patterns: Collection[str]) -> bool:
    """Match a tool name against a pattern set (exact, glob, MCP suffix).

    Shared matching semantics for every name-based policy surface:
    GOV-001 sensitive tools and the adversarial layer's tool-sequence
    categories."""
    if not tool:
        return False
    if tool in patterns:
        return True
    for entry in patterns:
        if ("*" in entry or "?" in entry) and fnmatch.fnmatchcase(tool, entry):
            return True
    if tool.startswith("mcp__"):
        parts = tool.split("__", 2)
        if len(parts) == 3 and parts[2] in patterns:
            return True
    return False


def _rule_high_risk_tool_access(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Block high-risk agents from using sensitive tools without approval.

    The sensitive-tool set is the operator-supplied
    ``context["sensitive_tools"]`` (injected from the layer's
    constructor) and falls back to ``DEFAULT_SENSITIVE_TOOLS``. Matching
    is exact-string, glob (entries containing ``*``/``?``), or MCP
    suffix (``mcp__<server>__<tool>`` matches an entry naming the bare
    tool) — see :func:`tool_name_matches`.
    """
    sensitive_tools = context.get("sensitive_tools") or DEFAULT_SENSITIVE_TOOLS
    action_tool = action.get("tool", "")
    return (
        profile.risk_tier in (RiskTier.HIGH, RiskTier.CRITICAL)
        and tool_name_matches(action_tool, sensitive_tools)
    )


# Words in a tool name that mark it as running code or moving money. A name is
# split on `_`, `-`, `.` and camelCase, so "stripePayout" and
# "mcp__bank__wire_transfer" both match while "display" does not.
_CODE_RUNNER_WORDS = frozenset({"repl", "interpreter"})
_CODE_RUN_VERBS = frozenset({"execute", "exec", "run", "eval"})
_CODE_RUN_OBJECTS = frozenset({
    "code", "python", "javascript", "js", "script", "cell", "snippet", "jupyter", "notebook",
})
# Tools that run checks over code rather than code the agent wrote.
_CODE_CHECK_WORDS = frozenset({
    "test", "tests", "lint", "linter", "check", "checker", "format", "formatter",
})
_PAYMENT_WORDS = frozenset({
    "pay", "payment", "payments", "payout", "payouts", "transfer", "refund", "charge",
    "withdraw", "withdrawal", "wire", "purchase", "checkout", "remit", "disburse", "money",
})
DEFAULT_FINANCIAL_THRESHOLD = 1000


def _name_words(tool: str) -> set[str]:
    """Lower-cased words of a tool name."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", tool)
    return {w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w}


def runs_code(tool: str, registered: Collection[str] | None = None) -> bool:
    """Whether a tool runs code: a registered tool, else a name like ``execute_code``."""
    if registered is not None:
        return tool_name_matches(tool, registered)
    words = _name_words(tool)
    if words & _CODE_CHECK_WORDS:
        return False
    return bool(words & _CODE_RUNNER_WORDS) or bool(
        words & _CODE_RUN_VERBS and words & _CODE_RUN_OBJECTS
    )


def moves_money(tool: str, registered: Collection[str] | None = None) -> bool:
    """Whether a tool makes payments: a registered tool, else a name like ``transfer_funds``."""
    if registered is not None:
        return tool_name_matches(tool, registered)
    return bool(_name_words(tool) & _PAYMENT_WORDS)


def _amount(arguments: Any) -> float | None:
    """The call's ``amount`` as a number, or None when it is missing or not numeric."""
    value = (arguments or {}).get("amount") if isinstance(arguments, dict) else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def _rule_code_execution_boundary(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Require approval to run code the agent wrote, unless the deployment is sandboxed.

    Fires on a call tagged ``runs_inline_code`` (an interpreter given its
    program inline: ``python -c``, ``node -e``, ``eval``, stdin or a heredoc;
    ``bash -c`` strings are classified as shell instead), on a
    code-runner tool (see :func:`runs_code`), or on an action passed directly
    with ``type: "code_execution"``. Routine commands and scripts on disk do
    not count.
    """
    if context.get("sandbox", False):
        return False
    if action.get("type") == "code_execution":
        return True
    categories = action.get("categories") or []
    return ToolCallCategory.RUNS_INLINE_CODE.value in categories or runs_code(
        action.get("tool", ""), context.get("code_execution_tools")
    )


def _rule_financial_threshold(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Require approval for a payment above ``context["financial_threshold"]``.

    A call is a payment when its trusted tool name says so (see
    :func:`moves_money`) or when it is passed directly with ``type:
    "financial"``. The amount comes from ``action["arguments"]["amount"]``;
    untrusted arguments are nested so they can never shadow the trusted
    top-level ``tool``/``type`` fields. A payment with no numeric amount does
    not trigger the rule. The threshold is in the tool's own unit, so set it
    in cents for an API that takes cents.
    """
    is_payment = action.get("type") == "financial" or moves_money(
        action.get("tool", ""), context.get("financial_tools")
    )
    amount = _amount(action.get("arguments"))
    threshold = context.get("financial_threshold", DEFAULT_FINANCIAL_THRESHOLD)
    return is_payment and amount is not None and amount > threshold


def _rule_multi_agent_escalation(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Require human oversight for multi-agent coordination with >3 agents.

    v0.8: the rule now reads the topology context populated by
    team-aware adapters (Agno team, Bedrock collaborators, CrewAI
    crew, AutoGen group chat). Pre-v0.8 it fired only on a
    synthetic ``action.type == "multi_agent_coordination"`` that no
    adapter actually produced.

    Backward compat: the old action-based path stays valid in
    parallel — a caller can still trigger the rule by passing
    ``action={"type": "multi_agent_coordination", "agent_count": ...}``.
    """
    legacy_match = (
        action.get("type") == "multi_agent_coordination"
        and action.get("agent_count", 0) > 3
    )
    if legacy_match:
        return True
    topology_ctx = context.get("topology_context") or {}
    topology = topology_ctx.get("topology") or {}
    members = topology.get("members") or []
    return len(members) > 3


def _rule_sensitive_data_egress(
    profile: AgentProfile, action: dict[str, Any], context: dict[str, Any]
) -> bool:
    """Require approval to send data externally once secrets were read in-session.

    Reads the adapter-derived ``categories`` on the current action and
    the session's ``tool_call_categories`` (which include the current
    call, so read-and-send in one shell pipeline counts too).
    """
    if ToolCallCategory.SENDS_EXTERNAL.value not in (action.get("categories") or []):
        return False
    return any(
        ToolCallCategory.READS_SENSITIVE.value in (tags or [])
        for tags in context.get("tool_call_categories") or []
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
        PolicyRule(
            rule_id="GOV-005",
            name="Sensitive Data Egress",
            description="External send after a sensitive read in-session requires approval",
            condition=_rule_sensitive_data_egress,
            decision=PolicyDecision.REQUIRE_APPROVAL,
            severity=0.80,
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
    sensitive_tools : set[str], optional
        Tool names the GOV-001 high-risk rule gates. Entries match
        exactly, as globs when they contain ``*``/``?`` (e.g.
        ``"mcp__db__*"``), or against the bare tool part of
        MCP-namespaced names. Defaults to ``DEFAULT_SENSITIVE_TOOLS``;
        unmatched tools are not gated. A per-call
        ``context["sensitive_tools"]`` overrides this.
    sandboxed : bool
        Declare that the agent's code runs in a sandbox, which turns
        GOV-002 off. Default False. ``context["sandbox"]`` overrides it.
    code_execution_tools : set[str], optional
        Tools GOV-002 treats as running code, matched like
        ``sensitive_tools``. Replaces the name heuristic (names such as
        ``execute_code`` or ``python_repl``); inline code in shell
        commands counts either way.
    financial_tools : set[str], optional
        Tools GOV-003 treats as payments. Replaces the name heuristic
        (names with words such as ``payment``, ``transfer`` or ``refund``).
    financial_threshold : float
        Payment amount above which GOV-003 requires approval. Default 1000.
    """

    def __init__(
        self,
        policy_set: str = "enterprise-default",
        custom_rules: list[PolicyRule] | None = None,
        enable_audit: bool = True,
        escalation_callback: Callable[..., Any] | None = None,
        sensitive_tools: set[str] | None = None,
        sandboxed: bool = False,
        code_execution_tools: set[str] | None = None,
        financial_tools: set[str] | None = None,
        financial_threshold: float = DEFAULT_FINANCIAL_THRESHOLD,
    ):
        self.policy_set_name = policy_set
        self._rules = list(DEFAULT_POLICIES.get(policy_set, []))
        if custom_rules:
            self._rules.extend(custom_rules)
        self.enable_audit = enable_audit
        self.escalation_callback = escalation_callback
        self._sensitive_tools = (
            frozenset(sensitive_tools)
            if sensitive_tools is not None
            else DEFAULT_SENSITIVE_TOOLS
        )
        # Deployment facts the rules read from context; a per-call context value wins.
        self._context_defaults: dict[str, Any] = {
            "sandbox": sandboxed,
            "code_execution_tools": (
                frozenset(code_execution_tools) if code_execution_tools is not None else None
            ),
            "financial_tools": (
                frozenset(financial_tools) if financial_tools is not None else None
            ),
            "financial_threshold": financial_threshold,
        }
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
        ctx = dict(context or {})
        # Inject the layer's sensitive-tool set so GOV-001 sees it,
        # without clobbering a per-call override the caller passed in.
        ctx.setdefault("sensitive_tools", self._sensitive_tools)
        for key, value in self._context_defaults.items():
            ctx.setdefault(key, value)
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
