"""Token usage is normalized per framework and totalled per model.

``input_tokens`` counts every input token, cached or not; the cache and
reasoning fields are parts of input and output that stay absent when no
source reports them, so a missing breakdown never reads as zero.
"""

from __future__ import annotations

from agentegrity.core.usage import TokenUsage, UsageLedger


def test_empty_ledger_reports_nothing() -> None:
    assert UsageLedger().to_dict() is None


def test_totals_sum_per_model_and_overall() -> None:
    ledger = UsageLedger()
    ledger.record("r1", "opus", TokenUsage(input_tokens=100, output_tokens=10), source="transcript")
    ledger.record("r2", "opus", TokenUsage(input_tokens=50, output_tokens=5), source="transcript")
    ledger.record("r3", "haiku", TokenUsage(input_tokens=7, output_tokens=1), source="transcript")
    usage = ledger.to_dict()
    assert usage is not None
    assert (usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]) == (157, 16, 173)
    assert usage["requests"] == 3
    assert usage["by_model"]["opus"]["input_tokens"] == 150
    assert usage["by_model"]["haiku"]["requests"] == 1


def test_repeated_key_replaces_instead_of_adding() -> None:
    ledger = UsageLedger()
    ledger.record("r1", "opus", TokenUsage(input_tokens=100, output_tokens=8),
                  source="transcript", complete=False)
    ledger.record("r1", "opus", TokenUsage(input_tokens=100, output_tokens=182),
                  source="transcript")
    usage = ledger.to_dict()
    assert usage is not None
    assert usage["output_tokens"] == 182
    assert usage["requests"] == 1
    assert usage["complete"] is True


def test_breakdowns_are_absent_until_a_source_reports_them() -> None:
    ledger = UsageLedger()
    ledger.record("r1", "m", TokenUsage(input_tokens=10, output_tokens=2), source="trace")
    usage = ledger.to_dict()
    assert usage is not None
    assert "cache_read_tokens" not in usage
    assert "reasoning_tokens" not in usage
    ledger.record("r2", "m", TokenUsage(input_tokens=10, output_tokens=2, cache_read_tokens=4),
                  source="trace")
    usage = ledger.to_dict()
    assert usage is not None
    assert usage["cache_read_tokens"] == 4


def test_any_incomplete_entry_marks_the_total_incomplete() -> None:
    ledger = UsageLedger()
    ledger.record("r1", "m", TokenUsage(input_tokens=1, output_tokens=1), source="transcript")
    ledger.record("r2", "m", TokenUsage(input_tokens=1, output_tokens=1),
                  source="transcript", complete=False)
    usage = ledger.to_dict()
    assert usage is not None and usage["complete"] is False


def test_mark_incomplete_flags_a_known_gap() -> None:
    ledger = UsageLedger()
    ledger.record("r1", "m", TokenUsage(input_tokens=1, output_tokens=1), source="transcript")
    ledger.mark_incomplete()
    usage = ledger.to_dict()
    assert usage is not None and usage["complete"] is False


def test_sources_are_listed_and_can_be_discarded() -> None:
    ledger = UsageLedger()
    ledger.record("t1", "m", TokenUsage(input_tokens=5, output_tokens=5), source="transcript")
    ledger.record("x1", "m", TokenUsage(input_tokens=9, output_tokens=9),
                  source="provider_response")
    usage = ledger.to_dict()
    assert usage is not None and usage["sources"] == ["provider_response", "transcript"]
    ledger.discard("transcript")
    usage = ledger.to_dict()
    assert usage is not None
    assert usage["sources"] == ["provider_response"]
    assert usage["input_tokens"] == 9


def test_unknown_model_is_grouped_as_unknown() -> None:
    ledger = UsageLedger()
    ledger.record("r1", None, TokenUsage(input_tokens=3, output_tokens=1), source="trace")
    usage = ledger.to_dict()
    assert usage is not None and list(usage["by_model"]) == ["unknown"]


def test_snapshot_entries_carry_their_own_request_count() -> None:
    ledger = UsageLedger()
    ledger.record("run-1", "gpt", TokenUsage(input_tokens=1300, output_tokens=40, requests=3),
                  source="provider_response")
    usage = ledger.to_dict()
    assert usage is not None and usage["requests"] == 3
