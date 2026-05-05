"""Loader for AgentDojo task suites.

AgentDojo organises tasks into a directory tree per *suite* (banking,
slack, travel, etc.). Each suite has a ``tasks.json`` listing
(user_task, injection_task) pairs. A useful detection-benchmark
projection is: every distinct injection_task is one labelled positive,
every user_task is one labelled negative.

This loader keeps to that minimal projection. AgentDojo's full
end-to-end semantics (executing a tool chain and grading the result)
are out of scope here — that belongs in a separate eval harness.

Returns ``[]`` if the path env var is unset or the directory is
missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.benchmarks.fixtures import BenchmarkPrompt


def load_agentdojo(root: str | Path | None = None) -> list[BenchmarkPrompt]:
    """Load AgentDojo prompts from ``root`` or ``AGENTEGRITY_BENCH_AGENTDOJO``.

    Walks ``root/<suite>/tasks.json`` and emits one
    :class:`BenchmarkPrompt` per ``user_prompt`` (negative) and one
    per ``injection`` field (positive). Suite name flows into the
    ``family`` and ``source`` fields.
    """
    base = Path(root or os.environ.get("AGENTEGRITY_BENCH_AGENTDOJO", ""))
    if not base or not base.is_dir():
        return []

    out: list[BenchmarkPrompt] = []
    for tasks_file in base.glob("*/tasks.json"):
        suite = tasks_file.parent.name
        try:
            tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            user_prompt = task.get("user_prompt") or task.get("prompt")
            if isinstance(user_prompt, str):
                out.append(
                    BenchmarkPrompt(
                        text=user_prompt,
                        is_attack=False,
                        family="benign",
                        source=f"agentdojo/{suite}",
                    )
                )
            injection = task.get("injection") or task.get("injection_prompt")
            if isinstance(injection, str):
                out.append(
                    BenchmarkPrompt(
                        text=injection,
                        is_attack=True,
                        family="prompt_injection",
                        source=f"agentdojo/{suite}",
                    )
                )
    return out


__all__ = ["load_agentdojo"]
