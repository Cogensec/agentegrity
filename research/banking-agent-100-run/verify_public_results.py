#!/usr/bin/env python3
"""Recompute the manuscript's outcome counts from the public, redacted run table.

This checks the published observations, not the underlying trace signatures or
the truth of the benchmark's scenario labels.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).with_name("PUBLIC_RUN_OUTCOMES_300.csv")
MODES = ("baseline", "protected", "protected_approve_all")
TRUE = {"True": True, "False": False, "": None, "None": None}


def _require(condition: bool, message: str) -> None:
    """A research-audit invariant cannot be disabled by python -O."""
    if not condition:
        raise ValueError(message)


def summarize(path: Path = DATA) -> dict:
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    _require(len(rows) == 300, f"expected 300 observations, found {len(rows)}")
    units: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for row in rows:
        scenario, iteration, mode = row["scenario"], int(row["iteration"]), row["mode"]
        _require(scenario in {f"S{i:02d}" for i in range(10)}, "unexpected scenario")
        _require(iteration in range(1, 11) and mode in MODES, "invalid run index or arm")
        _require(mode not in units[(scenario, iteration)], "duplicate paired unit")
        units[(scenario, iteration)][mode] = row
    _require(len(units) == 100, "expected exactly 100 paired units")
    _require(all(set(arms) == set(MODES) for arms in units.values()),
             "missing or extra experimental arm")
    _require(all(len({int(row["seed"]) for row in arms.values()}) == 1
                 for arms in units.values()), "seed mismatch within paired unit")
    _require(all(len({row["attack_present"] for row in arms.values()}) == 1
                 for arms in units.values()), "attack label changed between arms")
    _require(all(len({row["scenario"] for row in arms.values()}) == 1
                 for arms in units.values()), "scenario changed between arms")

    report = {"rows": len(rows), "paired_units": len(units), "arms": {}}
    for mode in MODES:
        arm = [row for row in rows if row["mode"] == mode]
        attack = [row for row in arm if TRUE[row["attack_present"]]]
        benign = [row for row in arm if not TRUE[row["attack_present"]]]
        report["arms"][mode] = {
            "runs": len(arm),
            "attack_runs": len(attack),
            "benign_runs": len(benign),
            "unsafe_attack_actions": sum(TRUE[row["unsafe_action"]] is True for row in attack),
            "benign_goals_completed": sum(TRUE[row["benign_goal_completed"]] is True for row in benign),
            "guard_blocks_on_attack": (
                sum(TRUE[row["guard_blocked"]] is True for row in attack) if mode != "baseline" else None
            ),
            "adversarial_flags_on_attack": (
                sum(TRUE[row["adversarial_flagged"]] is True for row in attack) if mode != "baseline" else None
            ),
            "adversarial_flags_on_benign": (
                sum(TRUE[row["adversarial_flagged"]] is True for row in benign) if mode != "baseline" else None
            ),
            "precheck_escalations": (
                sum(row["precheck_action"] == "escalate" for row in arm) if mode != "baseline" else None
            ),
            "mean_elapsed_ms": round(sum(float(row["elapsed_ms"]) for row in arm) / len(arm), 5),
        }
    report["paired_unsafe_transitions"] = {
        f"{str(a).lower()}->{str(b).lower()}": n
        for (a, b), n in sorted(Counter(
            (TRUE[arms["baseline"]["unsafe_action"]],
             TRUE[arms["protected"]["unsafe_action"]])
            for arms in units.values()
        ).items())
    }
    report["approve_all_unsafe_transitions"] = {
        f"{str(a).lower()}->{str(b).lower()}": n
        for (a, b), n in sorted(Counter(
            (TRUE[arms["protected"]["unsafe_action"]],
             TRUE[arms["protected_approve_all"]["unsafe_action"]])
            for arms in units.values()
        ).items())
    }
    report["s09_approve_all"] = {
        "checkpoint_returned": sum(
            TRUE[row["checkpoint_returned"]] is True
            for row in rows if row["scenario"] == "S09" and row["mode"] == "protected_approve_all"
        ),
        "business_ledger_restored": sum(
            TRUE[row["business_ledger_restored"]] is True
            for row in rows if row["scenario"] == "S09" and row["mode"] == "protected_approve_all"
        ),
    }
    # The result is an attribution check, not proof of agent security.
    # The ablation estimates what the scenario-coded approval callback adds:
    # the detector alone cannot be credited with stopping every bad action.
    protected = report["arms"]["protected"]
    approve_all = report["arms"]["protected_approve_all"]
    attack_count = protected["attack_runs"]
    report["attribution"] = {
        "source_of_preventive_denials": "scenario-coded synthetic approval callback",
        "adversarial_detection_recall_on_scripted_attacks": (
            protected["adversarial_flags_on_attack"] / attack_count
            if attack_count else None
        ),
        "protected_unsafe_attack_actions": protected["unsafe_attack_actions"],
        "approve_all_unsafe_attack_actions": approve_all["unsafe_attack_actions"],
        "external_identity_or_effect_verified": False,
    }
    _require(
        all(TRUE[arms["baseline"]["unsafe_action"]]
            == TRUE[arms["protected_approve_all"]["unsafe_action"]]
            for arms in units.values()),
        "approve-all ablation no longer mirrors baseline unsafe outcomes",
    )
    return report


if __name__ == "__main__":
    print(json.dumps(summarize(), indent=2, sort_keys=True))
