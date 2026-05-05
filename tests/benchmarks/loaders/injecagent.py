"""Loader for the InjecAgent benchmark.

InjecAgent (https://github.com/uiuc-kang-lab/InjecAgent) ships JSON
arrays under ``data/test_cases_<type>_base.json`` with fields::

    {
      "Attack Type": "Physical Harm" | "Financial Loss" | "Physical Data" | ...,
      "User Instruction": "<benign user task>",
      "Tool Response": "<JSON-string containing the embedded injection>",
      "Attacker Instruction": "<the injection itself, if extracted>",
      ...
    }

We project this to two BenchmarkPrompt rows per record:

  * ``Tool Response``   → labelled positive (model-context attack)
  * ``User Instruction`` → labelled negative (the original benign task)

The loader accepts a single file path (env var
``AGENTEGRITY_BENCH_INJECAGENT``) or a directory containing the
``test_cases_*_base.json`` files. Both JSON-array (real release) and
JSONL (informal subsets) formats are supported. Family attribution
uses the filename — ``_ds_`` files are tagged ``data_exfiltration``
because every Data-Stealing case is an exfil instruction; ``_dh_``
files are tagged ``prompt_injection`` because Direct-Harm cases are
arbitrary injected commands.

Returns ``[]`` when the env var is unset or the path doesn't exist.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.benchmarks.fixtures import BenchmarkPrompt


def _load_one_file(path: Path, family: str) -> list[BenchmarkPrompt]:
    """Parse one InjecAgent JSON file (array OR JSONL) into prompts."""
    text = path.read_text(encoding="utf-8")
    rows: list[dict[str, object]] = []
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
        except json.JSONDecodeError:
            return []
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except json.JSONDecodeError:
                continue

    out: list[BenchmarkPrompt] = []
    for row in rows:
        attack_text = row.get("Tool Response") or row.get("attacker_instruction")
        benign_text = row.get("User Instruction") or row.get("user_instruction")
        if isinstance(attack_text, str) and attack_text:
            out.append(
                BenchmarkPrompt(
                    text=attack_text,
                    is_attack=True,
                    family=family,
                    source=f"injecagent/{path.stem}",
                )
            )
        if isinstance(benign_text, str) and benign_text:
            out.append(
                BenchmarkPrompt(
                    text=benign_text,
                    is_attack=False,
                    family="benign",
                    source=f"injecagent/{path.stem}",
                )
            )
    return out


def _infer_family(filename: str) -> str:
    """InjecAgent file naming: test_cases_<type>_base.json with type
    in {dh = direct harm, ds = data stealing}. We map dh → prompt_injection
    (arbitrary injected commands) and ds → data_exfiltration (every
    case is a steal-and-send instruction)."""
    name = filename.lower()
    if "_ds_" in name or name.startswith("ds"):
        return "data_exfiltration"
    return "prompt_injection"


def load_injecagent(path: str | Path | None = None) -> list[BenchmarkPrompt]:
    """Load InjecAgent rows from ``path`` (file or dir) or
    ``AGENTEGRITY_BENCH_INJECAGENT``.

    Each row contributes up to two prompts. Missing fields tolerated
    quietly. Returns empty list when no path is configured or the
    target doesn't exist.
    """
    p = Path(path or os.environ.get("AGENTEGRITY_BENCH_INJECAGENT", ""))
    if not str(p) or not p.exists():
        return []

    if p.is_file():
        return _load_one_file(p, _infer_family(p.name))

    # Directory mode — load every test_cases_*_base.json (or *.json
    # if the maintainer renamed them).
    out: list[BenchmarkPrompt] = []
    for f in sorted(p.glob("test_cases_*_base.json")):
        out.extend(_load_one_file(f, _infer_family(f.name)))
    if not out:
        # Fall back to any *.json — this lets a fork that uses different
        # filenames work without monkey-patching.
        for f in sorted(p.glob("*.json")):
            out.extend(_load_one_file(f, _infer_family(f.name)))
    return out


__all__ = ["load_injecagent"]
