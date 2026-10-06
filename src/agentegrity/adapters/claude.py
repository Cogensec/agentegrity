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
        await client.query("...")
        async for message in client.receive_response():
            adapter.observe(message)  # optional: exact token totals

Token usage is read from the session transcript the hooks name, with no
code change. Passing each message to :meth:`ClaudeAdapter.observe` swaps
that estimate for the SDK's own per-model totals.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.adapters.transcripts import SOURCE as TRANSCRIPT_SOURCE
from agentegrity.adapters.transcripts import ClaudeTranscriptUsage
from agentegrity.core.usage import TokenUsage

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
    _usage_reader_type = ClaudeTranscriptUsage

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Totals restart after a conversation reset; each run of totals
        # gets its own keys so the earlier one is kept.
        self._result_generation = 0

    def observe(self, message: Any) -> None:
        """Record exact token usage from a message the SDK yielded (optional).

        ``ResultMessage.model_usage`` holds the session's running totals per
        model, subagents included. The first one replaces the transcript
        estimate for the rest of the session. A ``ConversationResetMessage``
        zeroes the SDK's totals, so the totals before it are kept.
        """
        if type(message).__name__ == "ConversationResetMessage":
            self._result_generation += 1
            return
        model_usage = getattr(message, "model_usage", None)
        if not isinstance(model_usage, Mapping):
            return
        if self._usage_reader is not None:
            self._usage_reader = None
            self._usage.discard(TRANSCRIPT_SOURCE)
        for model, usage in model_usage.items():
            if isinstance(usage, Mapping):
                self.record_usage(
                    f"result:{self._result_generation}:{model}", model, _model_usage(usage),
                    source="provider_response",
                )

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


def _model_usage(usage: Mapping[str, Any]) -> TokenUsage:
    """Normalize the CLI's ``modelUsage`` entry; its input count leaves out the cache."""
    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    cache_read = count("cacheReadInputTokens")
    cache_write = count("cacheCreationInputTokens")
    return TokenUsage(
        input_tokens=count("inputTokens") + cache_read + cache_write,
        output_tokens=count("outputTokens"),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        requests=None,
    )
