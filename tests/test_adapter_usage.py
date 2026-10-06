"""Adapters report token usage on turn-end events and in the session summary.

Usage is a running total: every ``stop`` event carries the session's
usage so far and the summary carries the final total. Coding-agent hosts
read it from the transcript their hooks name; the Claude Agent SDK can
also pass its result messages for exact totals.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.adapters.hook_hosts import ClaudeCodeAdapter, CodexAdapter
from agentegrity.core.profile import AgentProfile
from agentegrity.core.usage import TokenUsage
from agentegrity.hooks.session import HookSession

jsonschema = pytest.importorskip("jsonschema")
pytest.importorskip("referencing")
from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402

SCHEMAS = Path(__file__).parent.parent / "schemas" / "exporter"


class _Plain(_BaseAdapter):
    _name = "plain"


def _profile(model_id: str | None = None) -> AgentProfile:
    profile = AgentProfile.default(name="t")
    profile.model_id = model_id
    return profile


def _stop_usage(adapter: _BaseAdapter) -> dict[str, Any] | None:
    stop = next(e for e in reversed(adapter.events) if e.event_type == "stop")
    usage: dict[str, Any] | None = stop.data.get("usage")
    return usage


def _claude_line(request: str, out: int, model: str = "claude-opus-5-5") -> str:
    return json.dumps({
        "type": "assistant", "requestId": request,
        "message": {"id": request, "model": model, "stop_reason": "end_turn",
                    "usage": {"input_tokens": 3, "cache_read_input_tokens": 100,
                              "cache_creation_input_tokens": 10, "output_tokens": out}},
    }) + "\n"


def _append(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.writelines(lines)


class TestBaseAdapter:
    def test_stop_event_and_summary_carry_the_running_total(self) -> None:
        adapter = _Plain(profile=_profile(), stream_from_env=False)
        adapter.record_usage("c1", "gpt", TokenUsage(input_tokens=10, output_tokens=2),
                             source="provider_response")
        asyncio.run(adapter.on_event("stop", {}))
        stop = _stop_usage(adapter)
        assert stop is not None and stop["total_tokens"] == 12
        adapter.record_usage("c2", "gpt", TokenUsage(input_tokens=5, output_tokens=1),
                             source="provider_response")
        assert adapter.get_summary()["usage"]["total_tokens"] == 18

    def test_no_usage_means_no_usage_field(self) -> None:
        adapter = _Plain(profile=_profile(), stream_from_env=False)
        asyncio.run(adapter.on_event("stop", {}))
        assert _stop_usage(adapter) is None
        assert "usage" not in adapter.get_summary()

    def test_unnamed_model_falls_back_to_the_profile(self) -> None:
        adapter = _Plain(profile=_profile("configured-model"), stream_from_env=False)
        adapter.record_usage("c1", None, TokenUsage(input_tokens=1, output_tokens=1),
                             source="trace")
        assert list(adapter.get_summary()["usage"]["by_model"]) == ["configured-model"]


class TestClaudeCodeTranscript:
    def test_stop_reads_the_transcript(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        _append(transcript, _claude_line("r1", 20))
        adapter = ClaudeCodeAdapter(profile=_profile(), stream_from_env=False)
        asyncio.run(adapter.on_event("stop", {"transcript_path": str(transcript)}))
        stop = _stop_usage(adapter)
        assert stop is not None
        assert (stop["input_tokens"], stop["output_tokens"]) == (113, 20)

    def test_close_picks_up_lines_written_after_the_last_turn(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        _append(transcript, _claude_line("r1", 20))
        adapter = ClaudeCodeAdapter(profile=_profile(), stream_from_env=False)
        asyncio.run(adapter.on_event("stop", {"transcript_path": str(transcript)}))
        _append(transcript, _claude_line("r2", 5))
        adapter.close()
        assert adapter.get_summary()["usage"]["requests"] == 2

    def test_subagent_stop_reads_the_child_transcript(self, tmp_path: Path) -> None:
        child = tmp_path / "agent-a.jsonl"
        _append(child, _claude_line("c1", 7, model="claude-haiku-4-5"))
        adapter = ClaudeCodeAdapter(profile=_profile(), stream_from_env=False)
        asyncio.run(adapter.on_event("subagent_start", {"agent_id": "a"}))
        asyncio.run(adapter.on_event(
            "subagent_stop", {"agent_id": "a", "agent_transcript_path": str(child)}))
        asyncio.run(adapter.on_event("stop", {}))
        stop = _stop_usage(adapter)
        assert stop is not None and list(stop["by_model"]) == ["claude-haiku-4-5"]


def test_codex_reads_its_rollout(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    usage = {"input_tokens": 900, "cached_input_tokens": 400, "output_tokens": 30,
             "reasoning_output_tokens": 12, "total_tokens": 930}
    _append(rollout, json.dumps({"type": "token_usage_record",
                                 "payload": {"response_id": "resp_1", "usage": usage}}) + "\n")
    adapter = CodexAdapter(profile=_profile(), stream_from_env=False)
    asyncio.run(adapter.on_event("stop", {"transcript_path": str(rollout), "model": "gpt-5.5"}))
    stop = _stop_usage(adapter)
    assert stop is not None
    assert (stop["input_tokens"], stop["reasoning_tokens"]) == (900, 12)
    assert list(stop["by_model"]) == ["gpt-5.5"]


@dataclass
class ResultMessage:
    model_usage: dict[str, Any]


@dataclass
class ConversationResetMessage:
    new_conversation_id: str


def _model_usage(inputs: int, output: int) -> dict[str, Any]:
    return {"inputTokens": inputs, "outputTokens": output, "cacheReadInputTokens": 1000,
            "cacheCreationInputTokens": 100, "costUSD": 0.01}


class TestClaudeAgentSdk:
    def test_hooks_read_the_transcript(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        _append(transcript, _claude_line("r1", 20))
        adapter = ClaudeAdapter(profile=_profile(), stream_from_env=False)
        asyncio.run(adapter.on_event("stop", {"transcript_path": str(transcript)}))
        stop = _stop_usage(adapter)
        assert stop is not None and stop["sources"] == ["transcript"]

    def test_result_messages_replace_the_transcript_estimate(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        _append(transcript, _claude_line("r1", 20))
        adapter = ClaudeAdapter(profile=_profile(), stream_from_env=False)
        asyncio.run(adapter.on_event("stop", {"transcript_path": str(transcript)}))
        adapter.observe(ResultMessage(model_usage={"claude-opus-5-5": _model_usage(5, 40)}))
        adapter.observe(ResultMessage(model_usage={"claude-opus-5-5": _model_usage(9, 70)}))
        _append(transcript, _claude_line("r2", 500))
        adapter.close()
        usage = adapter.get_summary()["usage"]
        assert usage["sources"] == ["provider_response"]
        assert (usage["input_tokens"], usage["output_tokens"]) == (9 + 1000 + 100, 70)

    def test_a_reset_keeps_the_totals_from_before_it(self) -> None:
        adapter = ClaudeAdapter(profile=_profile(), stream_from_env=False)
        adapter.observe(ResultMessage(model_usage={"claude-opus-5-5": _model_usage(5, 40)}))
        adapter.observe(ConversationResetMessage(new_conversation_id="c2"))
        adapter.observe(ResultMessage(model_usage={"claude-opus-5-5": _model_usage(1, 3)}))
        assert adapter.get_summary()["usage"]["output_tokens"] == 43

    def test_other_messages_are_ignored(self) -> None:
        adapter = ClaudeAdapter(profile=_profile(), stream_from_env=False)
        adapter.observe(object())
        assert "usage" not in adapter.get_summary()


def test_hook_session_reports_usage_through_the_runtime(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    _append(transcript, _claude_line("r1", 20))
    session = HookSession("claude-code", "s-1", tmp_path / "claude-code" / "s-1.chain.json",
                          stream=False)
    session.handle({"session_id": "s-1", "hook_event_name": "Stop",
                    "transcript_path": str(transcript)})
    stop = _stop_usage(session._adapter)
    assert stop is not None and stop["requests"] == 1


def test_usage_matches_the_exporter_contract(tmp_path: Path) -> None:
    common = json.loads((SCHEMAS / "common.json").read_text())
    registry = Registry().with_resource("common.json", Resource.from_contents(common))
    transcript = tmp_path / "s.jsonl"
    _append(transcript, _claude_line("r1", 20))
    adapter = ClaudeCodeAdapter(profile=_profile(), stream_from_env=False)
    asyncio.run(adapter.on_event("stop", {"transcript_path": str(transcript)}))
    summary = adapter.get_summary()
    stop = next(e for e in adapter.events if e.event_type == "stop")
    end_schema = json.loads((SCHEMAS / "session_end.json").read_text())
    event_schema = json.loads((SCHEMAS / "event.json").read_text())
    Draft202012Validator(end_schema, registry=registry).validate(
        {"session_id": adapter.session_id, "summary": summary})
    Draft202012Validator(event_schema, registry=registry).validate(
        {"session_id": adapter.session_id, "event": stop.to_dict()})
    bad = {**summary, "usage": {**summary["usage"], "input_tokens": -1}}
    with pytest.raises(jsonschema.ValidationError):
        Draft202012Validator(end_schema, registry=registry).validate(
            {"session_id": adapter.session_id, "summary": bad})


class TestHookSessionRestart:
    """Each daemon lifetime reports only the tokens spent during it."""

    def _stop(self, session: HookSession, transcript: Path) -> dict[str, Any] | None:
        session.handle({"session_id": "s-1", "hook_event_name": "Stop",
                        "transcript_path": str(transcript)})
        return _stop_usage(session._adapter)

    def test_a_restarted_daemon_resumes_where_the_last_one_stopped(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        chain = tmp_path / "claude-code" / "s-1.chain.json"
        _append(transcript, _claude_line("r1", 20))
        first = HookSession("claude-code", "s-1", chain, stream=False)
        first_usage = self._stop(first, transcript)
        first.close()
        _append(transcript, _claude_line("r2", 5))
        second = HookSession("claude-code", "s-1", chain, stream=False)
        second_usage = self._stop(second, transcript)
        assert first_usage is not None and second_usage is not None
        assert (first_usage["output_tokens"], second_usage["output_tokens"]) == (20, 5)

    def test_read_state_is_private_to_the_user(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        chain = tmp_path / "claude-code" / "s-1.chain.json"
        _append(transcript, _claude_line("r1", 20))
        session = HookSession("claude-code", "s-1", chain, stream=False)
        self._stop(session, transcript)
        state = tmp_path / "claude-code" / "s-1.usage.json"
        assert state.stat().st_mode & 0o777 == 0o600
        assert "r1" in state.read_text() and "usage" not in json.loads(state.read_text())

    def test_a_corrupt_state_file_does_not_break_the_session(self, tmp_path: Path) -> None:
        transcript = tmp_path / "s.jsonl"
        chain = tmp_path / "claude-code" / "s-1.chain.json"
        chain.parent.mkdir()
        (tmp_path / "claude-code" / "s-1.usage.json").write_text("{not json")
        _append(transcript, _claude_line("r1", 20))
        session = HookSession("claude-code", "s-1", chain, stream=False)
        usage = self._stop(session, transcript)
        assert usage is not None and usage["requests"] == 1
