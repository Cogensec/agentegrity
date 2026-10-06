"""Usage read from coding-agent transcripts.

Claude Code writes one line per content block and repeats the request's
usage on each, so lines are keyed by ``requestId`` and the last one wins;
an early line carries a placeholder output count. Codex writes one
``token_usage_record`` per response, keyed by ``response_id``. Both
readers only read lines appended since the last read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentegrity.adapters.transcripts import ClaudeTranscriptUsage, CodexRolloutUsage
from agentegrity.core.usage import UsageLedger


def _write(path: Path, *records: dict[str, Any], partial: str = "") -> None:
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")
        fh.write(partial)


def _claude(request: str, *, out: int, stop: str | None = "end_turn",
            model: str = "claude-opus-5-5", input_tokens: int = 2,
            cache_read: int = 1000, cache_write: int = 50) -> dict[str, Any]:
    return {
        "type": "assistant",
        "requestId": request,
        "message": {
            "id": f"msg_{request}",
            "model": model,
            "stop_reason": stop,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
                "output_tokens": out,
            },
        },
    }


def _usage(ledger: UsageLedger) -> dict[str, Any]:
    usage = ledger.to_dict()
    assert usage is not None
    return usage


class TestClaudeTranscript:
    def test_cache_is_added_to_input(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=10))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        usage = _usage(ledger)
        assert usage["input_tokens"] == 2 + 1000 + 50
        assert (usage["cache_read_tokens"], usage["cache_write_tokens"]) == (1000, 50)
        assert "reasoning_tokens" not in usage
        assert usage["sources"] == ["transcript"]

    def test_repeated_lines_count_once_and_the_last_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=8, stop=None), _claude("r1", out=182, stop="tool_use"),
               _claude("r1", out=182, stop="tool_use"), _claude("r2", out=5))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        usage = _usage(ledger)
        assert usage["requests"] == 2
        assert usage["output_tokens"] == 187
        assert usage["complete"] is True

    def test_a_request_without_its_final_line_is_incomplete(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.jsonl"
        _write(path, _claude("r1", out=8, stop=None))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"agent_transcript_path": str(path)})
        reader.refresh()
        assert _usage(ledger)["complete"] is False

    def test_only_appended_lines_are_read(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=10), partial='{"type": "assist')
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        assert _usage(ledger)["requests"] == 1
        with path.open("a", encoding="utf-8") as fh:
            fh.write('ant"}\n')
        _write(path, _claude("r2", out=20))
        reader.refresh()
        usage = _usage(ledger)
        assert (usage["requests"], usage["output_tokens"]) == (2, 30)

    def test_a_rewritten_file_is_reread_without_double_counting(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=10), _claude("r2", out=10))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        path.unlink()
        _write(path, {"type": "system", "subtype": "compact_boundary"}, _claude("r3", out=1))
        reader.refresh()
        usage = _usage(ledger)
        assert (usage["requests"], usage["output_tokens"]) == (3, 21)
        assert usage["complete"] is True

    def test_a_transcript_that_starts_after_compaction_is_incomplete(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, {"type": "system", "subtype": "compact_boundary"}, _claude("r1", out=10))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        assert _usage(ledger)["complete"] is False

    def test_subagent_files_add_to_the_session(self, tmp_path: Path) -> None:
        main, child = tmp_path / "s.jsonl", tmp_path / "agent-a.jsonl"
        _write(main, _claude("r1", out=10))
        _write(child, _claude("c1", out=4, model="claude-haiku-4-5"))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(main), "agent_transcript_path": str(child)})
        reader.refresh()
        usage = _usage(ledger)
        assert set(usage["by_model"]) == {"claude-opus-5-5", "claude-haiku-4-5"}

    def test_synthetic_messages_and_missing_files_are_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=0, model="<synthetic>"))
        ledger = UsageLedger()
        reader = ClaudeTranscriptUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.note({"transcript_path": str(tmp_path / "missing.jsonl")})
        reader.refresh()
        assert ledger.to_dict() is None


def _turn_context(model: str) -> dict[str, Any]:
    return {"timestamp": "t", "type": "turn_context", "payload": {"model": model}}


def _codex_usage(**overrides: int) -> dict[str, int]:
    usage = {"input_tokens": 1000, "cached_input_tokens": 600, "cache_write_input_tokens": 0,
             "output_tokens": 50, "reasoning_output_tokens": 20, "total_tokens": 1050}
    return {**usage, **overrides}


def _record(response: str, **overrides: int) -> dict[str, Any]:
    usage = _codex_usage(**overrides)
    return {
        "timestamp": "t",
        "type": "token_usage_record",
        "payload": {"response_id": response, "turn_id": "t1", "usage": usage,
                    "turn_token_usage": usage, "thread_token_usage": usage},
    }


def _token_count(total: dict[str, int], last: dict[str, int]) -> dict[str, Any]:
    return {
        "timestamp": "t",
        "type": "event_msg",
        "payload": {"type": "token_count",
                    "info": {"total_token_usage": total, "last_token_usage": last}},
    }


class TestCodexRollout:
    def test_records_count_once_per_response(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        _write(path, _turn_context("gpt-5.5"), _record("resp_1"), _record("resp_1"),
               _record("resp_2", input_tokens=200, cached_input_tokens=0))
        ledger = UsageLedger()
        reader = CodexRolloutUsage(ledger)
        reader.note({"transcript_path": str(path), "model": "hook-model"})
        reader.refresh()
        usage = _usage(ledger)
        assert usage["requests"] == 2
        assert usage["input_tokens"] == 1200
        assert usage["cache_read_tokens"] == 600
        assert usage["reasoning_tokens"] == 40
        assert list(usage["by_model"]) == ["gpt-5.5"]
        assert usage["complete"] is True

    def test_model_falls_back_to_the_hook_payload(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        _write(path, _record("resp_1"))
        ledger = UsageLedger()
        reader = CodexRolloutUsage(ledger)
        reader.note({"transcript_path": str(path), "model": "gpt-5.5-codex"})
        reader.refresh()
        assert list(_usage(ledger)["by_model"]) == ["gpt-5.5-codex"]

    def test_token_count_is_ignored_when_records_exist(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        _write(path, _token_count(_codex_usage(), _codex_usage()), _record("resp_1"))
        ledger = UsageLedger()
        reader = CodexRolloutUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        usage = _usage(ledger)
        assert (usage["requests"], usage["sources"]) == (1, ["transcript"])

    def test_older_builds_fall_back_to_token_count(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        first, second = _codex_usage(), _codex_usage(input_tokens=500, total_tokens=550)
        running = _codex_usage(input_tokens=1500, total_tokens=1600, output_tokens=100)
        overflow = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 400000}
        _write(path, _token_count(first, first), _token_count(first, first),
               _token_count(running, second), _token_count(overflow, second))
        ledger = UsageLedger()
        reader = CodexRolloutUsage(ledger)
        reader.note({"transcript_path": str(path)})
        reader.refresh()
        usage = _usage(ledger)
        assert usage["requests"] == 2
        assert usage["input_tokens"] == 1500
        assert usage["complete"] is False

    def test_child_rollouts_add_to_the_session(self, tmp_path: Path) -> None:
        parent, child = tmp_path / "parent.jsonl", tmp_path / "child.jsonl"
        _write(parent, _record("resp_1"))
        _write(child, _turn_context("gpt-5.5-mini"), _record("resp_9", input_tokens=10))
        ledger = UsageLedger()
        reader = CodexRolloutUsage(ledger)
        reader.note({"transcript_path": str(parent), "agent_transcript_path": str(child)})
        reader.refresh()
        assert _usage(ledger)["requests"] == 2


class TestRestart:
    """A restarted daemon resumes from saved read state instead of re-reading."""

    def test_a_restored_reader_counts_only_new_lines(self, tmp_path: Path) -> None:
        main, child = tmp_path / "s.jsonl", tmp_path / "agent-a.jsonl"
        _write(main, _claude("r1", out=10), _claude("r2", out=10))
        _write(child, _claude("c1", out=3))
        first = ClaudeTranscriptUsage(UsageLedger())
        first.note({"transcript_path": str(main), "agent_transcript_path": str(child)})
        first.refresh()
        state = json.loads(json.dumps(first.state()))
        _write(main, _claude("r3", out=7))
        _write(child, _claude("c2", out=2))
        ledger = UsageLedger()
        second = ClaudeTranscriptUsage(ledger)
        second.restore(state)
        second.refresh()
        usage = _usage(ledger)
        assert (usage["requests"], usage["output_tokens"]) == (2, 9)

    def test_a_file_rewritten_while_down_resumes_after_the_last_counted_call(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=10), _claude("r2", out=10))
        first = ClaudeTranscriptUsage(UsageLedger())
        first.note({"transcript_path": str(path)})
        first.refresh()
        state = first.state()
        path.unlink()
        _write(path, {"type": "system", "subtype": "compact_boundary"},
               _claude("r2", out=10), _claude("r3", out=4))
        ledger = UsageLedger()
        second = ClaudeTranscriptUsage(ledger)
        second.restore(state)
        second.refresh()
        usage = _usage(ledger)
        assert (usage["requests"], usage["output_tokens"]) == (1, 4)
        assert usage["complete"] is True

    def test_a_replaced_file_without_the_last_call_is_counted_whole(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "s.jsonl"
        _write(path, _claude("r1", out=10))
        first = ClaudeTranscriptUsage(UsageLedger())
        first.note({"transcript_path": str(path)})
        first.refresh()
        state = first.state()
        path.unlink()
        _write(path, _claude("n1", out=1), _claude("n2", out=2))
        ledger = UsageLedger()
        second = ClaudeTranscriptUsage(ledger)
        second.restore(state)
        second.refresh()
        assert _usage(ledger)["requests"] == 2

    def test_codex_state_keeps_the_model_and_record_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "rollout.jsonl"
        _write(path, _turn_context("gpt-5.5"), _record("resp_1"))
        first = CodexRolloutUsage(UsageLedger())
        first.note({"transcript_path": str(path)})
        first.refresh()
        state = json.loads(json.dumps(first.state()))
        _write(path, _token_count(_codex_usage(), _codex_usage()), _record("resp_2"))
        ledger = UsageLedger()
        second = CodexRolloutUsage(ledger)
        second.restore(state)
        second.refresh()
        usage = _usage(ledger)
        assert (usage["requests"], list(usage["by_model"])) == (1, ["gpt-5.5"])

    def test_malformed_state_is_ignored(self) -> None:
        reader = ClaudeTranscriptUsage(UsageLedger())
        reader.restore({"tails": {"/x.jsonl": {"offset": "nope"}}, "models": 3})
        reader.restore("not a mapping")
        reader.refresh()
