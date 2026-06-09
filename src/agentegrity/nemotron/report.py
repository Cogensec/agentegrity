"""Render a :class:`BenchmarkReport` as JSON or operator-facing Markdown."""

from __future__ import annotations

import json

from agentegrity.nemotron.benchmark import CATEGORY_DISPLAY, CATEGORY_ORDER, BenchmarkReport


def to_json(report: BenchmarkReport) -> str:
    """Serialise the full report (including the attestation chain) to JSON."""
    return json.dumps(report.to_dict(), indent=2)


def to_markdown(report: BenchmarkReport) -> str:
    """Render the report in the operator-facing layout."""
    lines: list[str] = []
    lines.append("# Agentegrity Nemotron Benchmark Report\n")
    lines.append(f"Model: {report.model}")
    lines.append(f"Run ID: {report.run_id}")
    lines.append(f"Overall Score: {report.overall_score}/100")
    lines.append(f"Grade: {report.grade}")
    lines.append(f"Readiness: {report.readiness}\n")

    lines.append("Category Scores:")
    for cat in CATEGORY_ORDER:
        display = CATEGORY_DISPLAY[cat]
        score = report.category_scores.get(display)
        if score is not None:
            lines.append(f"- {display}: {score}")
    lines.append("")

    lines.append("Critical Findings:")
    if report.critical_findings:
        for finding in report.critical_findings:
            lines.append(f"- {finding}")
    else:
        lines.append("- None")
    lines.append("")

    if report.notes:
        lines.append("Notes:")
        for note in report.notes:
            lines.append(f"- {note}")
        lines.append("")

    ev = report.evidence
    lines.append("Evidence:")
    lines.append(
        f"- Signed receipts generated ({ev.get('signed_receipts', 0)})"
    )
    lines.append(
        "- Chain verification passed"
        if ev.get("chain_verified")
        else "- Chain verification FAILED"
    )
    lines.append(f"- Policy hash recorded ({_short(ev.get('policy_hash'))})")
    lines.append(f"- Prompt hash recorded ({_short(ev.get('prompt_hash'))})")
    lines.append(f"- Output hash recorded ({_short(ev.get('output_hash'))})")
    lines.append("")

    return "\n".join(lines)


def _short(value: object) -> str:
    text = str(value or "")
    return f"{text[:12]}…" if len(text) > 12 else text


__all__ = ["to_json", "to_markdown"]
