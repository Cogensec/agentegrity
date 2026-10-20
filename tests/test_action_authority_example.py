"""Smoke tests for the README-linked synthetic authority/evidence example."""

from __future__ import annotations

import runpy
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "action_authority_evidence.py"


def test_documented_example_enforces_scope_and_reconciles_only_verified_claims():
    module = runpy.run_path(str(EXAMPLE))
    assert module["run_demo"]() == ("denied", "pending", "confirmed")


def test_documented_example_cli_output(capsys):
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    output = capsys.readouterr().out
    assert "Out-of-scope action: denied" in output
    assert "Before simulated evidence verification: pending" in output
    assert "After simulated evidence verification: confirmed" in output
