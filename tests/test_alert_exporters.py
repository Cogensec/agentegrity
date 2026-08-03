"""Tests for the webhook alert exporters.

Transport is mocked at urllib level. Semantics under test: which
events fire an alert, payload shapes (Slack text vs structured JSON),
fail-open delivery, and flush-on-session-end.
"""

from __future__ import annotations

import io
import json
import urllib.request

import pytest

from agentegrity.adapters.base import FrameworkEvent
from agentegrity.core.evaluator import (
    IntegrityScore,
    LayerResult,
    PropertyScores,
)
from agentegrity.exporters.alerts import (
    SlackAlertExporter,
    WebhookAlertExporter,
)


def _score(action: str, composite: float = 0.42) -> IntegrityScore:
    return IntegrityScore(
        composite=composite,
        properties=PropertyScores(
            adversarial_coherence=composite,
            environmental_portability=1.0,
            verifiable_assurance=1.0,
            recovery_integrity=1.0,
        ),
        layer_results=[
            LayerResult(
                layer_name="adversarial",
                score=composite,
                passed=action == "pass",
                action=action,
                details={},
            )
        ],
    )


def _event(action: str, event_type: str = "pre_tool_use") -> FrameworkEvent:
    return FrameworkEvent(
        event_type=event_type,
        adapter_name="langchain",
        data={"tool_name": "payment_execute"},
        evaluation_result=_score(action),
    )


@pytest.fixture
def posts(monkeypatch):
    captured: list[tuple[urllib.request.Request, dict]] = []

    def fake_urlopen(req, timeout=None):
        captured.append((req, json.loads(req.data.decode("utf-8"))))
        return io.BytesIO(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


class TestAlertTriggering:
    def test_block_event_fires_alert(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event("s1", _event("block"))
        exporter.flush()
        assert len(posts) == 1

    def test_escalate_event_fires_alert(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event("s1", _event("escalate"))
        exporter.flush()
        assert len(posts) == 1

    def test_pass_and_alert_actions_silent_by_default(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event("s1", _event("pass"))
        exporter.on_event("s1", _event("alert"))
        exporter.flush()
        assert posts == []

    def test_custom_alert_actions(self, posts):
        exporter = WebhookAlertExporter(
            "https://hooks.example/x", alert_on={"alert", "block"}
        )
        exporter.on_event("s1", _event("alert"))
        exporter.flush()
        assert len(posts) == 1

    def test_event_without_evaluation_is_silent(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event(
            "s1", FrameworkEvent(event_type="stop", adapter_name="x")
        )
        exporter.flush()
        assert posts == []


class TestPayloadShapes:
    def test_json_payload_structure(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event("s1", _event("block"))
        exporter.flush()
        _, body = posts[0]
        assert body["action"] == "block"
        assert body["session_id"] == "s1"
        assert body["adapter"] == "langchain"
        assert body["event_type"] == "pre_tool_use"
        assert body["composite"] == 0.42
        assert "adversarial" in body["failing_layers"]

    def test_slack_payload_is_text(self, posts):
        exporter = SlackAlertExporter("https://hooks.slack.com/services/x")
        exporter.on_event("s1", _event("block"))
        exporter.flush()
        req, body = posts[0]
        assert set(body) == {"text"}
        assert "block" in body["text"]
        assert "langchain" in body["text"]
        assert req.get_header("Content-type") == "application/json"

    def test_describe_reports_target_without_secrets(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x?token=abc")
        desc = exporter.describe()
        assert desc["type"] == "WebhookAlertExporter"
        assert "token=abc" not in desc["target"]


class TestFailureSemantics:
    def test_network_error_fails_open(self, monkeypatch):
        def broken(req, timeout=None):
            raise OSError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", broken)
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event("s1", _event("block"))
        exporter.flush()  # must not raise

    def test_session_end_flushes(self, posts):
        exporter = WebhookAlertExporter("https://hooks.example/x")
        exporter.on_event("s1", _event("block"))
        exporter.on_session_end("s1", {"evaluations": 3})
        assert len(posts) == 1
