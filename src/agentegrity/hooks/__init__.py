"""Stateful hook runtime for coding-agent hosts (Claude Code, Codex)."""

from agentegrity.hooks.daemon import chain_path, run_hook, serve
from agentegrity.hooks.protocol import HOSTS, HookEvent, normalize, render_decision, verdict_for
from agentegrity.hooks.session import HookSession

__all__ = [
    "HOSTS",
    "HookEvent",
    "HookSession",
    "chain_path",
    "normalize",
    "render_decision",
    "run_hook",
    "serve",
    "verdict_for",
]
