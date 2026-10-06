"""Adapters for coding-agent hosts instrumented through command hooks.

Claude Code and Codex call a command on every hook event; the shared
runtime in :mod:`agentegrity.hooks` translates those payloads and feeds
these adapters. The adapters add nothing to :class:`_BaseAdapter` except
the framework name the console groups sessions by and the reader for the
token usage each host writes to its transcripts.
"""

from __future__ import annotations

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.adapters.transcripts import ClaudeTranscriptUsage, CodexRolloutUsage


class ClaudeCodeAdapter(_BaseAdapter):
    """A Claude Code session instrumented through its command hooks."""

    _name = "claude_code"
    _usage_reader_type = ClaudeTranscriptUsage


class CodexAdapter(_BaseAdapter):
    """A Codex session instrumented through its command hooks."""

    _name = "codex"
    _usage_reader_type = CodexRolloutUsage


ADAPTERS_BY_HOST: dict[str, type[_BaseAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
}
