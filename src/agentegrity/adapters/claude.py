"""
Claude Agent SDK adapter for agentegrity.

Instruments agents built on the Claude Agent SDK by registering hooks
at eight integration points. Inherits all event-handling / evaluation /
attestation machinery from ``_BaseAdapter`` — this module only defines
the SDK-specific hook callbacks and the ``create_hooks()`` bridge.

Usage:
    from agentegrity.adapters.claude import ClaudeAdapter
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    adapter = ClaudeAdapter(profile=my_profile)
    options = ClaudeAgentOptions(hooks=adapter.create_hooks())
    async with ClaudeSDKClient(options=options) as client:
        ...
"""

from __future__ import annotations

import logging
from typing import Any

from agentegrity.adapters.base import _BaseAdapter

logger = logging.getLogger("agentegrity.adapters.claude")


def _make_hook(adapter: ClaudeAdapter, event_type: str) -> Any:
    async def _hook(
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await adapter.on_event(event_type, input_data)
        except Exception as exc:
            logger.warning("%s hook failed: %s", event_type, exc, exc_info=True)
            return {}

    return _hook


class ClaudeAdapter(_BaseAdapter):
    """Instruments a Claude Agent SDK agent with agentegrity evaluation."""

    _name = "claude"

    def create_hooks(self) -> dict[str, list[Any]]:
        """Create Claude Agent SDK hook configuration.

        Returns a dict suitable for passing to ``ClaudeAgentOptions(hooks=...)``.
        Imports ``HookMatcher`` at call time so the adapter module itself
        can be imported without the ``claude-agent-sdk`` dependency.
        """
        try:
            from claude_agent_sdk import HookMatcher
        except ImportError:
            raise ImportError(
                "claude-agent-sdk is required for the Claude adapter. "
                "Install it with: pip install agentegrity[claude]"
            ) from None

        return {
            "PreToolUse": [HookMatcher(hooks=[_make_hook(self, "pre_tool_use")])],
            "PostToolUse": [HookMatcher(hooks=[_make_hook(self, "post_tool_use")])],
            "PostToolUseFailure": [
                HookMatcher(hooks=[_make_hook(self, "post_tool_use_failure")])
            ],
            "UserPromptSubmit": [
                HookMatcher(hooks=[_make_hook(self, "user_prompt_submit")])
            ],
            "Stop": [HookMatcher(hooks=[_make_hook(self, "stop")])],
            "SubagentStart": [HookMatcher(hooks=[_make_hook(self, "subagent_start")])],
            "SubagentStop": [HookMatcher(hooks=[_make_hook(self, "subagent_stop")])],
            "PreCompact": [HookMatcher(hooks=[_make_hook(self, "pre_compact")])],
        }
