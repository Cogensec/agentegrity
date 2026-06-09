"""Tests for the `agentegrity nemotron benchmark` CLI path."""

from __future__ import annotations

import json
from pathlib import Path

from agentegrity.__main__ import main


def test_cli_runs_mock_benchmark(tmp_path: Path) -> None:
    out = tmp_path / "report"
    rc = main([
        "nemotron", "benchmark", "--mock", "--report",
        "--output-dir", str(out),
    ])
    assert rc == 0
    results = json.loads((out / "results.json").read_text())
    assert results["overall_score"] == 100
    assert results["grade"] == "A"
    assert (out / "report.md").exists()
    assert results["evidence"]["chain_verified"] is True


def test_cli_usage_errors() -> None:
    assert main(["nemotron"]) == 2
    assert main(["nemotron", "benchmark", "--endpoint"]) == 2
    assert main(["nemotron", "benchmark", "--bogus"]) == 2


def test_cli_missing_profile_is_handled(tmp_path: Path) -> None:
    rc = main([
        "nemotron", "benchmark", "--mock",
        "--profile", str(tmp_path / "does-not-exist.yaml"),
    ])
    assert rc == 2
