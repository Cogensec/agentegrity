#!/usr/bin/env python3
"""Render manuscript figures from the public 300-row outcome export."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FIGURES = ROOT / "figures"
DATA = ROOT / "PUBLIC_RUN_OUTCOMES_300.csv"
FONT_PAIRS = [
    (Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
     Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
     Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    (Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
     Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")),
]
INK = "#19211F"
MUTED = "#5B6762"
PAPER = "#FBFAF6"
GRID = "#D9DED8"
RED = "#B65E46"
GREEN = "#3C7868"
PURPLE = "#716385"
GOLD = "#A77819"


def font(size: int, bold: bool = False):
    for regular, weight in FONT_PAIRS:
        candidate = weight if bold else regular
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    raise RuntimeError("No supported TrueType font found; install DejaVu Sans or Liberation Sans")


def image(width: int, height: int):
    canvas = Image.new("RGB", (width, height), PAPER)
    return canvas, ImageDraw.Draw(canvas)


def text(draw, xy, value, size=26, color=INK, bold=False):
    draw.text(xy, value, font=font(size, bold), fill=color)


def load():
    with DATA.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def scenario_chart(rows):
    names = {
        "S00": "Own-account transfer", "S01": "Approved high-value transfer",
        "S02": "Prompt redirect", "S03": "Tool-output directive",
        "S04": "Identity bypass", "S05": "Missing approval",
        "S06": "Reasoning mismatch", "S07": "Memory substitution",
        "S08": "Record exposure", "S09": "Altered beneficiary",
    }
    counts = defaultdict(int)
    for row in rows:
        counts[(row["scenario"], row["mode"])] += row["unsafe_action"] == "True"
    canvas, draw = image(1800, 1060)
    text(draw, (66, 54), "Unsafe outcomes by scripted scenario", 48, bold=True)
    text(draw, (68, 120), "Ten seeded repetitions per scenario and configuration", 28, MUTED)
    legend = [("Baseline", RED), ("Agentegrity + scenario-coded approval", GREEN),
              ("Agentegrity + approve-all callback", PURPLE)]
    x = 68
    for label, color in legend:
        draw.rounded_rectangle((x, 180, x + 22, 202), 5, fill=color)
        text(draw, (x + 33, 177), label, 24)
        x += 190 if label == "Baseline" else 535
    left, width = 545, 1135
    for i in range(11):
        gx = left + i * width / 10
        draw.line((gx, 235, gx, 985), fill=GRID, width=2)
        text(draw, (gx - 9, 995), str(i), 20, MUTED)
    for j, scenario in enumerate(names):
        y = 247 + j * 73
        text(draw, (70, y + 10), scenario, 24, GOLD, bold=True)
        text(draw, (146, y + 10), names[scenario], 24)
        for k, (mode, color) in enumerate([
            ("baseline", RED), ("protected", GREEN),
            ("protected_approve_all", PURPLE),
        ]):
            val = counts[(scenario, mode)]
            bar_y = y + k * 15
            if val:
                draw.rounded_rectangle((left, bar_y, left + val * width / 10, bar_y + 11), 4, fill=color)
        draw.line((68, y + 68, 1680, y + 68), fill=GRID, width=1)
    text(draw, (68, 1030), "Counts are fixture outcomes; bars do not identify which control caused a stop.", 20, MUTED)
    canvas.save(FIGURES / "04_three_arm_outcomes.png", optimize=True)


def control_chart(rows):
    arm = defaultdict(list)
    for row in rows:
        arm[row["mode"]].append(row)
    measures = [
        ("Prechecks escalated", lambda r: r["precheck_action"] == "escalate", 100),
        ("Attack actions denied by callback", lambda r: r["attack_present"] == "True" and r["guard_blocked"] == "True", 80),
        ("Attack runs with adversarial threat", lambda r: r["attack_present"] == "True" and r["adversarial_flagged"] == "True", 80),
        ("Benign runs escalated", lambda r: r["attack_present"] == "False" and r["precheck_action"] == "escalate", 20),
    ]
    canvas, draw = image(1800, 690)
    text(draw, (66, 48), "Detection, escalation, and denial are different events", 44, bold=True)
    text(draw, (68, 108), "Primary protected configuration compared with the exploratory approve-all callback", 27, MUTED)
    x0, maxw = 720, 920
    for i, (label, predicate, denominator) in enumerate(measures):
        y = 190 + i * 115
        text(draw, (68, y + 21), label, 26)
        for j, (mode, color) in enumerate([("protected", GREEN), ("protected_approve_all", PURPLE)]):
            count = sum(predicate(r) for r in arm[mode])
            by = y + j * 34
            draw.rounded_rectangle((x0, by, x0 + maxw, by + 22), 8, fill="#E6E9E3")
            if count:
                draw.rounded_rectangle((x0, by, x0 + maxw * count / denominator, by + 22), 8, fill=color)
            text(draw, (x0 + maxw + 20, by - 5), f"{count}/{denominator}", 23, INK, True)
        draw.line((68, y + 94, 1715, y + 94), fill=GRID, width=1)
    draw.rounded_rectangle((68, 636, 91, 659), 5, fill=GREEN)
    text(draw, (104, 633), "Scenario-coded callback", 22)
    draw.rounded_rectangle((520, 636, 543, 659), 5, fill=PURPLE)
    text(draw, (556, 633), "Approve-all callback", 22)
    canvas.save(FIGURES / "05_control_attribution.png", optimize=True)


if __name__ == "__main__":
    FIGURES.mkdir(exist_ok=True)
    data = load()
    scenario_chart(data)
    control_chart(data)
    print("Rendered figures 04 and 05 from", len(data), "public rows.")
