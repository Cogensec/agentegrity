"""Adapters for coding-agent hosts instrumented through command hooks.

Claude Code and Codex call a command on every hook event; the shared
runtime in :mod:`agentegrity.hooks` translates those payloads and feeds
these adapters. The adapters add nothing to :class:`_BaseAdapter` except
the framework name the console groups sessions by.
"""

from __future__ import annotations

from agentegrity.adapters.base import _BaseAdapter


class ClaudeCodeAdapter(_BaseAdapter):
    """A Claude Code session instrumented through its command hooks."""

    _name = "claude_code"


class CodexAdapter(_BaseAdapter):
    """A Codex session instrumented through its command hooks."""

    _name = "codex"


ADAPTERS_BY_HOST: dict[str, type[_BaseAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
}
