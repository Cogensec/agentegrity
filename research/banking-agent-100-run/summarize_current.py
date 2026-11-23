#!/usr/bin/env python3
"""Export descriptive tables and a chart from the actual paired run."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "outputs" / "paired-current-20261020"
ROWS = [json.loads(s) for s in (DATA / "results.jsonl").read_text().splitlines()]
GROUPS = defaultdict(list)
for row in ROWS:
    GROUPS[(row["scenario"], row["mode"])].append(row)

fields = ["run_id", "scenario", "attack_family", "attack_present", "iteration", "seed", "mode",
          "unsafe_action", "business_goal_completed", "guard_blocked", "attack_detected_by_adversarial",
          "monitor_evaluations", "chain_length", "chain_hash_valid", "trusted_signature_valid",
          "decision_links_valid", "monitor_checkpoint_restored", "business_ledger_restored", "elapsed_ms"]
with (ROOT / "results_paired_200_actual_runs.csv").open("w", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=fields)
    writer.writeheader()
    writer.writerows({k: row.get(k) for k in fields} for row in ROWS)

scenario_rows = []
for n in range(10):
    sid = f"S{n:02d}"
    b, p = GROUPS[(sid, "baseline")], GROUPS[(sid, "protected")]
    assert len(b) == len(p) == 10
    scenario_rows.append({
        "scenario": sid, "baseline_unsafe": sum(r["unsafe_action"] for r in b),
        "protected_unsafe": sum(r["unsafe_action"] for r in p),
        "protected_blocked": sum(r["guard_blocked"] for r in p),
        "adversarial_flagged": sum(r["attack_detected_by_adversarial"] for r in p),
    })
with (ROOT / "scenario_outcomes_current.csv").open("w", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=list(scenario_rows[0]))
    writer.writeheader()
    writer.writerows(scenario_rows)

W, H = 1100, 590
parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
         '<rect width="100%" height="100%" fill="#fff"/>',
         '<style>text{font-family:Arial,sans-serif;fill:#182433}.title{font-size:23px;font-weight:700}.sub{font-size:14px}.axis{font-size:12px}.num{font-size:12px;font-weight:700}</style>',
         '<text class="title" x="70" y="39">Observed outcomes in 200 paired synthetic banking runs</text>',
         '<text class="sub" x="70" y="63">10 deterministic seeded replays per scenario · current Agentegrity source + scripted approval callback</text>']
colors = {"baseline_unsafe": "#bb3b38", "protected_blocked": "#b58a20", "adversarial_flagged": "#277f69"}
labels = {"baseline_unsafe": "Baseline unsafe", "protected_blocked": "Protected blocked", "adversarial_flagged": "Adversarial flagged"}
for idx, key in enumerate(colors):
    x = 75 + idx * 190
    parts += [f'<rect x="{x}" y="80" width="15" height="15" fill="{colors[key]}"/>',
              f'<text class="sub" x="{x+22}" y="93">{labels[key]}</text>']
plot_left, plot_top, plot_height, group = 75, 140, 315, 96
for tick in range(0, 11, 2):
    y = plot_top + plot_height - tick / 10 * plot_height
    parts += [f'<line x1="{plot_left}" y1="{y:.1f}" x2="1040" y2="{y:.1f}" stroke="#dce2e8"/>',
              f'<text class="axis" x="{plot_left-18}" y="{y+4:.1f}" text-anchor="end">{tick}</text>']
for i, row in enumerate(scenario_rows):
    center = plot_left + 42 + i * group
    for j, key in enumerate(colors):
        val = row[key]
        x = center - 24 + j * 19
        h = val / 10 * plot_height
        y = plot_top + plot_height - h
        parts.append(f'<rect x="{x}" y="{y:.1f}" width="16" height="{h:.1f}" fill="{colors[key]}"/>')
        if val:
            parts.append(f'<text class="num" x="{x+8}" y="{y-6:.1f}" text-anchor="middle">{val}</text>')
    parts.append(f'<text class="axis" x="{center+1}" y="{plot_top+plot_height+23}" text-anchor="middle">{row["scenario"]}</text>')
parts += ['<text class="sub" x="70" y="513">All protected blocks followed escalation and denial by the scenario-coded callback; detection is a separate endpoint.</text>',
          '<text class="sub" x="70" y="538">No live bank, real customer, LLM agent, or independent human approval was involved.</text>', '</svg>']
(ROOT / "figures" / "03_paired_actual_outcomes.svg").write_text("\n".join(parts) + "\n")
print(f"Exported {len(ROWS)} run rows and {len(scenario_rows)} scenario summaries")
