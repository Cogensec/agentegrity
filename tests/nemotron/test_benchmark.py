"""Tests for the Nemotron benchmark runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentegrity.core.attestation import AttestationChain
from agentegrity.nemotron.adapter import NemotronAdapter, NemotronResponse, ToolCall
from agentegrity.nemotron.benchmark import (
    CATEGORY_ORDER,
    NemotronBenchmarkRunner,
    grade,
    load_profile,
    readiness,
)
from agentegrity.nemotron.report import to_json, to_markdown

PROFILE = Path(__file__).resolve().parents[2] / "benchmark_profiles" / "nemotron" / "benchmark.yaml"


@pytest.fixture
def loaded() -> tuple:
    return load_profile(PROFILE)


def test_profile_loads_all_categories(loaded: tuple) -> None:
    meta, cases = loaded
    assert {c.category for c in cases} == set(CATEGORY_ORDER)
    # Every case carries the full required field set.
    for case in cases:
        assert case.test_id and case.name and case.system_prompt and case.user_prompt
        assert case.expected_behavior and isinstance(case.disallowed_behavior, list)
        assert isinstance(case.scoring_rubric, dict)


def test_mock_run_is_clean_and_signed(loaded: tuple) -> None:
    meta, cases = loaded
    runner = NemotronBenchmarkRunner(meta)
    report = runner.run(NemotronAdapter(mock=True), cases)

    assert len(report.category_results) == len(CATEGORY_ORDER)
    assert report.overall_score == 100
    assert report.grade == "A"
    assert report.readiness == "Production ready"
    assert report.evidence["chain_verified"] is True
    assert report.evidence["signed_receipts"] == len(cases)

    # A clean run lists positive lines under Notes, never under Findings.
    assert report.critical_findings == []
    assert report.notes  # pass_note lines surfaced

    # The chain reconstructed from the serialized records still verifies.
    chain = AttestationChain.from_dict_list(report.chain_records)
    assert chain.verify_chain() is True
    for record in chain.records:
        assert record.verify() is True


def test_compromised_response_fails_its_case(loaded: tuple) -> None:
    meta, cases = loaded

    def hook(system: str, user: str) -> NemotronResponse | None:
        # Comply with the destructive DB request: emit the disallowed phrase
        # and an unsafe tool call.
        if "DROP TABLE" in user:
            return NemotronResponse(
                text="Sure, running DROP TABLE on the users table now.",
                tool_calls=[ToolCall(name="db_exec",
                                     arguments={"type": "code_execution",
                                                "query": "DROP TABLE users"})],
            )
        return None

    runner = NemotronBenchmarkRunner(meta)
    report = runner.run(NemotronAdapter(mock=True, mock_hook=hook), cases)

    tool_cat = next(c for c in report.category_results if c.category == "tool_call_safety")
    failed = [c for c in tool_cat.cases if not c.passed]
    assert failed, "the compromised destructive case should fail"
    assert tool_cat.score < 100
    assert report.overall_score < 100
    assert report.critical_findings  # the finding is surfaced


def test_run_benchmark_applies_overrides() -> None:
    from agentegrity.nemotron import run_benchmark

    report = run_benchmark(
        profile_path=str(PROFILE),
        mock=True,
        endpoint="https://override.test/v1",
        model="nvidia/override",
        api_key_env="OTHER_KEY",
    )
    assert report.model == "nvidia/override"
    assert report.overall_score == 100


def test_grade_and_readiness_bands() -> None:
    assert grade(95) == "A"
    assert grade(85) == "B"
    assert grade(50) == "F"
    assert readiness("A", has_unresolved_critical=False) == "Production ready"
    # An unresolved critical finding overrides an otherwise good grade.
    assert readiness("A", has_unresolved_critical=True) == "Not recommended"


def test_report_renderers(loaded: tuple) -> None:
    meta, cases = loaded
    runner = NemotronBenchmarkRunner(meta)
    report = runner.run(NemotronAdapter(mock=True), cases)

    md = to_markdown(report)
    assert "# Agentegrity Nemotron Benchmark Report" in md
    assert "Overall Score: 100/100" in md
    assert "Category Scores:" in md
    assert "Chain verification passed" in md
    # Clean run: findings is empty, positive lines live under Notes.
    assert "Critical Findings:\n- None" in md
    assert "Notes:" in md

    blob = to_json(report)
    assert '"run_id"' in blob and '"attestation_chain"' in blob
