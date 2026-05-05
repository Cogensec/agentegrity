"""Loader for the PINT (Prompt Injection Test) dataset.

PINT publishes a JSONL file where each row has at least:
    {
      "prompt": "<text>",
      "label": "injection" | "benign",
      "category": "<optional sub-family>",
    }

We adapt that format to :class:`BenchmarkPrompt`. Schema variations
(label spelled differently, an extra wrapper key) are handled with
defensive get() calls and a tolerant label-truth heuristic so a future
PINT release with a slightly different schema doesn't break the
loader silently — set ``AGENTEGRITY_BENCH_PINT_SCHEMA=strict`` to fail
loudly on unexpected rows.

Returns ``[]`` when the env var is unset or the file is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.benchmarks.fixtures import BenchmarkPrompt

_ATTACK_LABELS = {"injection", "attack", "malicious", "1", 1, True}


def load_pint(path: str | Path | None = None) -> list[BenchmarkPrompt]:
    """Load PINT-format prompts from ``path`` or ``AGENTEGRITY_BENCH_PINT``.

    Returns an empty list if no path is configured or the file does
    not exist — the caller (harness or pytest skip) is expected to
    handle the empty case.
    """
    p = Path(path or os.environ.get("AGENTEGRITY_BENCH_PINT", ""))
    if not p or not p.is_file():
        return []

    strict = os.environ.get("AGENTEGRITY_BENCH_PINT_SCHEMA") == "strict"
    out: list[BenchmarkPrompt] = []
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError(f"PINT row {line_no} is not JSON: {exc}") from exc
            continue
        text = row.get("prompt") or row.get("text") or row.get("input")
        if not isinstance(text, str):
            if strict:
                raise ValueError(f"PINT row {line_no} missing prompt")
            continue
        label = row.get("label") or row.get("class")
        is_attack = label in _ATTACK_LABELS
        family = row.get("category") or row.get("family") or "prompt_injection"
        out.append(
            BenchmarkPrompt(
                text=text,
                is_attack=bool(is_attack),
                family=str(family) if is_attack else "benign",
                source="pint",
            )
        )
    return out


__all__ = ["load_pint"]
