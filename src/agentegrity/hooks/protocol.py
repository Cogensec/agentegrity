"""Host hook protocols: map Claude Code and Codex hook payloads to adapter events.

Both hosts send one JSON object per hook on stdin with the same core
fields (``session_id``, ``hook_event_name``, ``tool_name``,
``tool_input``, ``tool_response``). They differ in three places:

* Claude Code reports a failed tool call as its own ``PostToolUseFailure``
  event. Codex has no such event: the failure arrives in ``PostToolUse``
  with a non-zero ``tool_response.exit_code``.
* Claude Code enforces ``permissionDecision: "ask"`` (an approval prompt).
  Codex parses ``ask`` but only enforces ``deny``, so an escalation fails
  closed there.
* Codex hooks are stateless per call and may run on Windows; the shared
  runtime handles both hosts the same way otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

HOSTS = ("claude-code", "codex")

# Hook event name -> adapter event type. SessionEnd closes the session.
_EVENTS = {
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "PostToolUseFailure": "post_tool_use_failure",
    "UserPromptSubmit": "user_prompt_submit",
    "Stop": "stop",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "PreCompact": "pre_compact",
    "SessionEnd": "session_end",
}

# Score action -> verdict, per host. Anything not listed allows.
_VERDICTS = {
    "claude-code": {"block": "deny", "escalate": "ask"},
    "codex": {"block": "deny", "escalate": "deny"},
}

# How much of a failed command's output the error message keeps.
_ERROR_OUTPUT_CHARS = 500


@dataclass(frozen=True)
class HookEvent:
    """A host hook payload translated into an adapter event."""

    kind: str
    data: dict[str, Any]


def parse_payload(raw: str) -> dict[str, Any] | None:
    """Parse a hook's stdin, or None when it is not a JSON object with a session."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    return payload


def normalize(host: str, payload: Mapping[str, Any]) -> HookEvent | None:
    """Translate one hook payload into an adapter event, or None to ignore it."""
    kind = _EVENTS.get(str(payload.get("hook_event_name", "")))
    if kind is None:
        return None
    data = dict(payload)
    if kind in ("pre_tool_use", "post_tool_use", "post_tool_use_failure"):
        if not isinstance(data.get("tool_name"), str) or not data["tool_name"]:
            return None
        if not isinstance(data.get("tool_input"), dict):
            data["tool_input"] = {}
    if host == "codex" and kind == "post_tool_use":
        error = _codex_failure(data.get("tool_response"))
        if error is not None:
            return HookEvent("post_tool_use_failure", {**data, "error": error})
    return HookEvent(kind, data)


def _codex_failure(tool_response: Any) -> str | None:
    """Codex reports a failed command as PostToolUse with a non-zero exit code."""
    if not isinstance(tool_response, Mapping):
        return None
    exit_code = tool_response.get("exit_code")
    if not isinstance(exit_code, int) or exit_code == 0:
        return None
    output = tool_response.get("output") or tool_response.get("stderr") or ""
    return f"exit {exit_code}: {str(output)[-_ERROR_OUTPUT_CHARS:]}".rstrip(": ")


def verdict_for(host: str, action: str) -> str:
    """Map a score action (pass/alert/escalate/block) to allow/ask/deny for a host."""
    return _VERDICTS[host].get(action, "allow")


def render_decision(verdict: str, reason: str) -> dict[str, Any] | None:
    """Build the PreToolUse output for a verdict; None means stay silent (allow)."""
    if verdict == "allow":
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict,
            "permissionDecisionReason": reason,
        }
    }
