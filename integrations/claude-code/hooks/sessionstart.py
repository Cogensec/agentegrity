#!/usr/bin/env python3
"""Agentegrity SessionStart hook for Claude Code.

The PreToolUse hook fails open: if ``agentegrity`` is not importable for
the ``python3`` on PATH, tool calls proceed unevaluated and the only
signal is a stderr line the user never sees. For a security plugin that
is the worst failure mode — it looks installed and protects nothing.

This hook runs once at session start, probes importability, and surfaces
a loud, user-visible banner (via the ``systemMessage`` field) when the
library is missing, so a broken install can never masquerade as an
active one. When the library is importable it stays silent.

Fail-open like every agentegrity surface: any error here is swallowed so
the hook can never block a session from starting.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Mapping


def _library_usable() -> bool:
    """True iff ``agentegrity`` actually imports for this interpreter.

    ``importlib.util.find_spec`` only proves the package is discoverable,
    not that it imports — a broken dependency (e.g. a mis-built native
    wheel) is discoverable but crashes on import, and such a crash can be
    a ``BaseException`` subclass (rust ``PanicException``) that in-process
    guards miss. Probing in a subprocess with the same interpreter is both
    faithful to what the PreToolUse hook will do and immune to the crash.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import agentegrity"],
            capture_output=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def build_status(env: Mapping[str, str]) -> dict[str, Any] | None:
    """Return a hook-output dict with a warning when the library is
    missing or the plugin is disabled-yet-unprotected, else None."""
    if env.get("AGENTEGRITY_CC_DISABLED") == "1":
        return {
            "systemMessage": (
                "agentegrity: disabled (AGENTEGRITY_CC_DISABLED=1). "
                "Tool calls in this session are NOT being verified."
            )
        }

    if _library_usable():
        # Library imports cleanly — the PreToolUse hook will do its job.
        # Stay silent so a healthy install adds no noise at session start.
        return None

    mode = env.get("AGENTEGRITY_CC_MODE", "enforce")
    return {
        "systemMessage": (
            "⚠ agentegrity: the 'agentegrity' library does not import "
            "for this python3, so this session is NOT being verified (the "
            "PreToolUse hook is failing open and every tool call proceeds "
            "unchecked).\n"
            f"  Configured mode: {mode}\n"
            "  Fix: pip install \"agentegrity[crypto]\" for the python3 on "
            "your PATH, then run /agentegrity-init to verify the wiring.\n"
            "  Confirm any time with /agentegrity-status."
        )
    }


def main() -> int:
    try:
        # SessionStart delivers a JSON payload on stdin; we do not need
        # any of its fields, but read it so the pipe drains cleanly.
        try:
            sys.stdin.read()
        except (OSError, ValueError):
            pass
        output = build_status(os.environ)
        if output is not None:
            json.dump(output, sys.stdout)
    except Exception as exc:  # noqa: BLE001 — must never break session start
        print(f"agentegrity sessionstart hook failed open: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
