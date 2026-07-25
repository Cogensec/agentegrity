"""Tests for the anonymous telemetry core: opt-out, identity, scoping, leak guards."""

from __future__ import annotations

import os
import queue
import stat
import time
import urllib.request
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentegrity.core import _telemetry_props as props
from agentegrity.core import telemetry
from agentegrity.core.attestation import AttestationChain, build_attestation_record
from agentegrity.core.evaluator import IntegrityScore, LayerResult, PropertyScores
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier


def make_profile(name: str = "test-agent") -> AgentProfile:
    return AgentProfile(
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use", "memory_access"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.HIGH,
        name=name,
    )


def make_score(composite: float = 0.85) -> IntegrityScore:
    return IntegrityScore(
        composite=composite,
        properties=PropertyScores(0.9123456, 0.8, 0.95, 0.6),
        layer_results=[
            LayerResult(layer_name="adversarial", score=0.9, passed=True, action="pass")
        ],
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Re-enable telemetry (conftest disables it globally) with HOME at tmp_path."""
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("AGENTEGRITY_TELEMETRY_DISABLED", raising=False)
    monkeypatch.setattr(telemetry, "_disabled", False)
    monkeypatch.setattr(telemetry, "_anonymous_id", None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Intercept the sender so events land in a list instead of a queue."""
    events: list[dict] = []

    class FakeSender:
        def enqueue(self, payload: dict) -> None:
            events.append(payload)

        def stop(self) -> None:
            pass

    monkeypatch.setattr(telemetry, "_sender", FakeSender())
    return events


class TestOptOut:
    @pytest.mark.parametrize("var", ["DO_NOT_TRACK", "AGENTEGRITY_TELEMETRY_DISABLED"])
    @pytest.mark.parametrize("value", ["1", "true", "YES", "On", "t", "y"])
    def test_env_vars_disable(self, enabled, monkeypatch, var, value):
        monkeypatch.setenv(var, value)
        assert telemetry._should_disable()

    @pytest.mark.parametrize("value", ["0", "false", "", "no"])
    def test_falsy_values_do_not_disable(self, enabled, monkeypatch, value):
        monkeypatch.setenv("DO_NOT_TRACK", value)
        assert not telemetry._should_disable()

    def test_no_id_file_created_when_disabled(self, enabled, monkeypatch):
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        assert telemetry._get_or_create_anonymous_id() is None
        assert not (enabled / ".agentegrity").exists()

    def test_capture_dropped_when_disabled(self, enabled, sent, monkeypatch):
        monkeypatch.setenv("AGENTEGRITY_TELEMETRY_DISABLED", "yes")
        with telemetry.telemetry_run_context():
            telemetry.telemetry_capture("event")
        assert sent == []

    def test_runtime_kill_switch(self, enabled, sent):
        telemetry.disable_telemetry()
        with telemetry.telemetry_run_context():
            telemetry.telemetry_capture("event")
        assert sent == []

    def test_geoip_flag(self, enabled, sent, monkeypatch):
        monkeypatch.setenv("AGENTEGRITY_TELEMETRY_DISABLE_GEOIP", "1")
        with telemetry.telemetry_run_context():
            telemetry.telemetry_capture("event")
        assert sent[0]["properties"]["$geoip_disable"] is True


class TestAnonymousId:
    def test_id_persists_across_calls(self, enabled, monkeypatch):
        first = telemetry._get_or_create_anonymous_id()
        monkeypatch.setattr(telemetry, "_anonymous_id", None)  # drop the memory cache
        second = telemetry._get_or_create_anonymous_id()
        assert first == second
        assert (enabled / ".agentegrity" / "id").read_text() == first

    def test_id_file_mode_is_0600(self, enabled):
        telemetry._get_or_create_anonymous_id()
        mode = stat.S_IMODE((enabled / ".agentegrity" / "id").stat().st_mode)
        assert mode == 0o600

    def test_existing_file_wins_creation_race(self, enabled):
        id_dir = enabled / ".agentegrity"
        id_dir.mkdir()
        (id_dir / "id").write_text("winner-uuid")
        assert telemetry._get_or_create_anonymous_id() == "winner-uuid"

    def test_unwritable_home_falls_back_to_ephemeral(self, enabled, monkeypatch):
        def deny(*args, **kwargs):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(os, "open", deny)
        anon = telemetry._get_or_create_anonymous_id()
        assert anon is not None and anon.startswith("anon-")


class TestScoping:
    def test_capture_outside_context_is_dropped(self, enabled, sent):
        telemetry.telemetry_capture("orphan")
        assert sent == []

    def test_capture_inside_context_is_sent(self, enabled, sent):
        with telemetry.telemetry_run_context():
            telemetry.telemetry_capture("event", properties={"count": 3})
        payload = sent[0]
        assert payload["event"] == "event"
        assert payload["properties"]["count"] == 3
        assert payload["properties"]["$session_id"] == telemetry._process_session_id
        assert payload["properties"]["python_version"].count(".") == 1

    def test_nested_exception_emitted_once_class_name_only(self, enabled, sent):
        with pytest.raises(ValueError):
            with telemetry.telemetry_run_context():
                with telemetry.telemetry_run_context():
                    raise ValueError("secret user data")
        exc_events = [e for e in sent if e["event"] == "agentegrity_uncaught_exception"]
        assert len(exc_events) == 1
        assert exc_events[0]["properties"]["exception_type"] == "ValueError"
        assert "secret" not in repr(sent)

    def test_tags_merged_into_captures(self, enabled, sent):
        with telemetry.telemetry_run_context():
            telemetry.telemetry_tag("component", "test")
            telemetry.telemetry_capture("event")
        assert sent[0]["properties"]["component"] == "test"

    def test_tag_outside_context_is_noop(self, enabled, sent):
        telemetry.telemetry_tag("component", "test")
        with telemetry.telemetry_run_context():
            telemetry.telemetry_capture("event")
        assert "component" not in sent[0]["properties"]

    def test_scoped_telemetry_sync(self, enabled, sent):
        @telemetry.scoped_telemetry
        def work() -> str:
            telemetry.telemetry_capture("sync_event")
            return "done"

        assert work() == "done"
        assert sent[0]["event"] == "sync_event"

    @pytest.mark.asyncio
    async def test_scoped_telemetry_async(self, enabled, sent):
        @telemetry.scoped_telemetry
        async def work() -> str:
            telemetry.telemetry_capture("async_event")
            return "done"

        assert await work() == "done"
        assert sent[0]["event"] == "async_event"


PROPERTY_NAMES = {f.name for f in fields(PropertyScores)}
ALLOWED_KEYS = PROPERTY_NAMES | {
    "risk_tier",
    "agent_type",
    "deployment_context",
    "capability_count",
    "composite",
    "composite_bucket",
    "passed",
    "action",
    "record_count",
    "decision_count",
    "verified",
    "record_kind",
    "signed",
    "evidence_count",
    "adapter",
}


class TestShapeBuilders:
    def all_shapes(self) -> list[dict]:
        profile = make_profile(name="x" * 500)  # hostile: names must never be sent
        score = make_score()
        record = build_attestation_record(profile, score)
        chain = AttestationChain()
        chain.append(build_attestation_record(profile, score))
        return [
            props.profile_shape(profile),
            props.score_shape(score),
            props.chain_shape(chain, verified=True),
            props.record_shape(record),
            props.adapter_shape("langchain"),
        ]

    def test_keys_stay_within_allowlist(self):
        for shape in self.all_shapes():
            assert set(shape) <= ALLOWED_KEYS, f"unexpected keys: {set(shape) - ALLOWED_KEYS}"

    def test_no_string_longer_than_64_chars(self):
        for shape in self.all_shapes():
            for value in shape.values():
                if isinstance(value, str):
                    assert len(value) <= 64

    def test_score_shape_values(self):
        shape = props.score_shape(make_score(0.65))
        assert shape["adversarial_coherence"] == 0.912
        assert shape["composite_bucket"] == "0.5-0.7"
        assert props.score_shape(make_score(0.3))["composite_bucket"] == "<0.5"
        assert props.score_shape(make_score(0.95))["composite_bucket"] == ">=0.9"

    def test_lowest_property(self):
        assert props.lowest_property(make_score()) == "recovery_integrity"


class TestSender:
    def test_queue_full_drops_silently(self, monkeypatch):
        sender = telemetry._PosthogSender()
        monkeypatch.setattr(sender, "_ensure_thread", lambda: None)
        for i in range(150):  # queue maxsize is 100 — the overflow must not raise
            sender.enqueue({"event": f"e{i}"})
        assert sender._queue.qsize() == 100

    def test_http_failure_never_propagates(self, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("network down")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        telemetry._PosthogSender()._post([{"event": "e"}])

    def test_post_sends_batch_envelope(self, monkeypatch):
        seen: list = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        def fake_urlopen(request, timeout):
            seen.append((request, timeout))
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        telemetry._PosthogSender()._post([{"event": "e", "distinct_id": "d"}])
        request, timeout = seen[0]
        assert timeout == 2
        assert request.full_url == f"{telemetry.HOST}/batch/"
        body = request.data.decode()
        assert '"api_key"' in body and '"batch"' in body

    def test_stop_with_full_queue_does_not_raise(self):
        sender = telemetry._PosthogSender()
        for i in range(100):
            sender._queue.put_nowait({"event": f"e{i}"})
        sender.stop()
        with pytest.raises(queue.Full):
            sender._queue.put_nowait({})  # still full — stop() must not have blocked on it

    def test_flush_at_exit_without_thread_returns(self):
        telemetry._PosthogSender()._flush_at_exit()

    def test_worker_thread_posts_enqueued_events(self, monkeypatch):
        posted: list[list[dict]] = []
        monkeypatch.setattr(telemetry, "_LINGER_SECONDS", 0.01)
        sender = telemetry._PosthogSender()
        monkeypatch.setattr(sender, "_post", posted.append)
        sender.enqueue({"event": "e1"})
        for _ in range(200):
            if posted:
                break
            time.sleep(0.01)
        sender._flush_at_exit()
        assert posted and posted[0][0]["event"] == "e1"
        assert sender._thread is not None and not sender._thread.is_alive()

    def test_get_sender_is_singleton(self, monkeypatch):
        monkeypatch.setattr(telemetry, "_sender", None)
        assert telemetry._get_sender() is telemetry._get_sender()
