"""Loader for the InjecAgent benchmark.

InjecAgent ships a JSONL file where each row has fields like::

    {
      "User Instruction": "<benign>",
      "Attacker Instruction": "<malicious>",
      "Tool Response": "<text containing the attack payload>",
      "Attack Type": "DH" | "DS",
    }

We project this to two BenchmarkPrompt rows per JSON record: the
``Tool Response`` is the labelled positive (the layer's
memory_reads / tool_outputs scan should detect it), and the
``User Instruction`` is the labelled negative.

Returns ``[]`` when the env var is unset or the file is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.benchmarks.fixtures import BenchmarkPrompt


def load_injecagent(path: str | Path | None = None) -> list[BenchmarkPrompt]:
    """Load InjecAgent rows from ``path`` or ``AGENTEGRITY_BENCH_INJECAGENT``.

    Each input row contributes up to two prompts (one benign, one
    attack). Missing fields are tolerated quietly so the loader works
    against both the public release and team-internal subsets.
    """
    p = Path(path or os.environ.get("AGENTEGRITY_BENCH_INJECAGENT", ""))
    if not p or not p.is_file():
        return []

    out: list[BenchmarkPrompt] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        attack_text = row.get("Tool Response") or row.get("attacker_instruction")
        benign_text = row.get("User Instruction") or row.get("user_instruction")
        attack_type = (row.get("Attack Type") or "").lower()
        # InjecAgent's attack types: DH = Direct Harm, DS = Data Stealing.
        family = "data_exfiltration" if attack_type == "ds" else "prompt_injection"
        if isinstance(attack_text, str) and attack_text:
            out.append(
                BenchmarkPrompt(
                    text=attack_text,
                    is_attack=True,
                    family=family,
                    source="injecagent",
                )
            )
        if isinstance(benign_text, str) and benign_text:
            out.append(
                BenchmarkPrompt(
                    text=benign_text,
                    is_attack=False,
                    family="benign",
                    source="injecagent",
                )
            )
    return out


__all__ = ["load_injecagent"]
