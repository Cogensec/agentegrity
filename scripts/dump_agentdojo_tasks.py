#!/usr/bin/env python
"""Dump AgentDojo task suites into the shape load_agentdojo() reads.

AgentDojo distributes its tasks as Python classes inside the
``agentdojo`` PyPI package, not as data files. This script projects
them to ``<out>/<suite>/tasks.json`` rows — one ``user_prompt``
(labelled negative) per user task and one ``injection`` (labelled
positive) per injection-task GOAL — so the detection benchmark can
consume them via ``AGENTEGRITY_BENCH_AGENTDOJO``.

Usage::

    pip install agentdojo
    python scripts/dump_agentdojo_tasks.py [out_dir] [--version v1.2.1]
    export AGENTEGRITY_BENCH_AGENTDOJO="$(pwd)/tests/benchmarks/data/agentdojo"

Caveat: this projection strips the injection *context* (which tool
result the attack rides in on) and keeps only the injected goal text.
Bare goal text and legitimate user tasks overlap heavily, so
content-only detectors score structurally low here — see STATUS.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out_dir",
        nargs="?",
        default="tests/benchmarks/data/agentdojo",
        help="Output directory (default: tests/benchmarks/data/agentdojo)",
    )
    parser.add_argument(
        "--version",
        default="v1.2.1",
        help="AgentDojo benchmark version (default: v1.2.1)",
    )
    args = parser.parse_args()

    try:
        from agentdojo.task_suite.load_suites import get_suites
    except ImportError:
        raise SystemExit(
            "agentdojo is not installed. Install with: pip install agentdojo"
        )

    root = Path(args.out_dir)
    for name, suite in get_suites(args.version).items():
        rows: list[dict[str, str]] = [
            {"user_prompt": task.PROMPT} for task in suite.user_tasks.values()
        ]
        rows.extend(
            {"injection": task.GOAL} for task in suite.injection_tasks.values()
        )
        suite_dir = root / name
        suite_dir.mkdir(parents=True, exist_ok=True)
        (suite_dir / "tasks.json").write_text(
            json.dumps(rows, indent=1), encoding="utf-8"
        )
        print(f"{name}: {len(rows)} rows -> {suite_dir / 'tasks.json'}")


if __name__ == "__main__":
    main()
