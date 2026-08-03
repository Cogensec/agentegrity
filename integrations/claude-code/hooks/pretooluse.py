#!/usr/bin/env python3
"""Agentegrity PreToolUse hook for Claude Code.

Every tool call is evaluated in-process before it executes — no
backend, no network, no GPU. The adversarial layer scans the tool
arguments for injection and exfiltration shapes; the governance layer
gates sensitive tool names (MCP-aware). The verdict maps to Claude
Code's permission flow:

    allow  →  no output (the normal permission flow proceeds)
    ask    →  inline approval prompt with the reason
    deny   →  the tool call is blocked before it runs

Every evaluated call appends a hash-linked DecisionRecord to a
per-session chain at ``~/.agentegrity/claude-code/<session>.chain.json``
(override the directory with ``AGENTEGRITY_CC_CHAIN_DIR``). Verify a
session afterwards with::

    agentegrity verify-decisions ~/.agentegrity/claude-code/<session>.chain.json

Configuration (environment):

    AGENTEGRITY_CC_MODE        enforce (default) | alert (log/record only)
    AGENTEGRITY_CC_DISABLED    1 disables the hook entirely
    AGENTEGRITY_CC_RISK_TIER   low|medium|high|critical (default high)
    AGENTEGRITY_CC_CHAIN_DIR   decision-chain directory override

Fail-open by design: if agentegrity is not importable, the payload is
malformed, or chain persistence fails, the hook stays silent and the
normal Claude Code permission flow decides.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping

# Tool arguments are structured (JSON) by construction, so the
# action_injection patterns that key on structure markers lose their
# discriminative power here — ordinary code and commit messages would
# trip them. The content-flavoured patterns stay active.
_STRUCTURE_CUE_PATTERNS = frozenset(
    {
        "embedded_polite_directive",
        "embedded_field_imperative",
        "spliced_capitalized_imperative",
    }
)

# Ask when a threat this strong surfaces but nothing warranted a block.
_ASK_SEVERITY = 0.70
_ASK_CONFIDENCE = 0.60


def _extract_text(value: Any) -> list[str]:
    """Collect every string value nested in a tool_input structure."""
    found: list[str] = []
    if isinstance(value, str):
        if value:
            found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_extract_text(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_extract_text(item))
    return found


def _record_decision(
    env: Mapping[str, str],
    session_id: str,
    tool_name: str,
    tool_input: Any,
    verdict: str,
    reasons: list[str],
) -> None:
    """Append a DecisionRecord to the per-session chain. Best-effort."""
    from agentegrity.core.attestation import AttestationChain
    from agentegrity.core.decision import build_decision_record

    chain_dir = env.get("AGENTEGRITY_CC_CHAIN_DIR") or os.path.join(
        os.path.expanduser("~"), ".agentegrity", "claude-code"
    )
    os.makedirs(chain_dir, exist_ok=True)
    chain_path = os.path.join(chain_dir, f"{session_id}.chain.json")

    chain = AttestationChain()
    if os.path.exists(chain_path):
        with open(chain_path, encoding="utf-8") as fh:
            chain = AttestationChain.from_json(fh.read())

    record = build_decision_record(
        agent_id=f"claude-code:{session_id}",
        decision_point="pre_tool_use",
        candidate_action={"tool": tool_name, "arguments": tool_input},
        reasoning_chain=[f"verdict:{verdict}", *reasons],
    )
    chain.append(record)
    tmp_path = chain_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        fh.write(chain.to_json())
    os.replace(tmp_path, chain_path)


def evaluate_tool_call(
    payload: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any] | None:
    """Evaluate one PreToolUse payload. Returns the hook output dict
    for ask/deny, or None to stay silent (allow). Never raises."""
    try:
        if env.get("AGENTEGRITY_CC_DISABLED") == "1":
            return None
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return None
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        session_id = str(payload.get("session_id") or "unknown-session")

        try:
            from agentegrity.core.profile import AgentProfile, RiskTier
            from agentegrity.layers.adversarial import (
                AdversarialLayer,
                default_detector_patterns,
            )
            from agentegrity.layers.governance import GovernanceLayer
        except ImportError:
            print(
                "agentegrity is not installed for this python; hook fails "
                "open. pip install agentegrity",
                file=sys.stderr,
            )
            return None

        try:
            risk = RiskTier(env.get("AGENTEGRITY_CC_RISK_TIER", "high"))
        except ValueError:
            risk = RiskTier.HIGH
        profile = AgentProfile.default(name="claude-code")
        profile.risk_tier = risk

        adversarial = AdversarialLayer(
            patterns=[
                p
                for p in default_detector_patterns()
                if p.name not in _STRUCTURE_CUE_PATTERNS
            ]
        )
        governance = GovernanceLayer(policy_set="enterprise-default")

        scan_text = "\n".join(_extract_text(tool_input))
        adv_result = adversarial.evaluate(profile, {"input": scan_text})
        gov_result = governance.evaluate(
            profile,
            {
                "action": {
                    "tool": tool_name,
                    "type": "tool_call",
                    "arguments": tool_input,
                }
            },
        )

        threats = adv_result.details.get("threats", [])
        reasons = [
            f"{t['threat_type']} (severity {t['severity']:.2f}): "
            f"{t.get('description', '')}"
            for t in threats
        ]
        ask_worthy = any(
            t.get("severity", 0.0) >= _ASK_SEVERITY
            and t.get("confidence", 0.0) >= _ASK_CONFIDENCE
            for t in threats
        )

        if adv_result.action == "block" or gov_result.action == "block":
            verdict = "deny"
        elif ask_worthy or gov_result.action == "escalate":
            verdict = "ask"
            if gov_result.action == "escalate":
                triggered = [
                    e.get("rule_id", "")
                    for e in gov_result.details.get("evaluations", [])
                    if e.get("triggered")
                ]
                reasons.append(
                    f"governance: {', '.join(triggered)} requires approval"
                )
        else:
            verdict = "allow"

        try:
            _record_decision(
                env, session_id, tool_name, tool_input, verdict, reasons
            )
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            print(f"agentegrity chain write failed: {exc}", file=sys.stderr)

        if verdict == "allow" or env.get("AGENTEGRITY_CC_MODE") == "alert":
            return None
        reason_text = "; ".join(reasons) or "agentegrity policy"
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": verdict,
                "permissionDecisionReason": f"agentegrity: {reason_text}",
            }
        }
    except Exception as exc:  # noqa: BLE001 — the hook must never break a session
        print(f"agentegrity hook failed open: {exc}", file=sys.stderr)
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    output = evaluate_tool_call(payload, os.environ)
    if output is not None:
        json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
