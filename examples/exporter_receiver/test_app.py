"""Smoke tests for the reference SessionExporter HTTP receiver.

Driven through FastAPI's TestClient so the same transport that real
agentegrity adapters speak is exercised end-to-end. Each endpoint is
tested twice: with a valid payload (expects 202) and with a malformed
payload (expects 422 with detail).

Run from the repo root::

    pip install -r examples/exporter_receiver/requirements.txt
    pytest examples/exporter_receiver/test_app.py -v
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

if importlib.util.find_spec("fastapi") is None:
    pytest.skip(
        "fastapi not installed; run "
        "`pip install -r examples/exporter_receiver/requirements.txt` "
        "to enable this example's tests",
        allow_module_level=True,
    )


# Ensure the package import root is the repo root so
# `examples.exporter_receiver.app` resolves cleanly when this test is
# run from elsewhere.
from pathlib import Path  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from examples.exporter_receiver.app import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _valid_session_start() -> dict[str, object]:
    return {
        "session_id": "0123456789abcdef0123456789abcdef",
        "adapter_name": "claude",
        "profile": {
            "agent_id": "agent-1",
            "name": "test",
            "agent_type": "tool_using",
            "capabilities": ["tool_use"],
            "deployment_context": "cloud",
            "risk_tier": "medium",
        },
    }


def _valid_event(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "event": {
            "event_type": "user_prompt_submit",
            "timestamp": "2026-05-05T12:00:00Z",
            "adapter_name": "claude",
            "data": {"prompt": "hi"},
            "evaluation_result": None,
        },
    }


def _valid_session_end(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "summary": {
            "adapter": "claude",
            "agent_id": "agent-1",
            "evaluations": 0,
            "events": 1,
            "attestation_records": 0,
            "chain_valid": True,
            "enforce_mode": False,
        },
    }


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestSessionStart:
    def test_valid_session_start_accepted(self, client: TestClient) -> None:
        r = client.post("/sessions", json=_valid_session_start())
        assert r.status_code == 202
        assert r.json() == {"accepted": True}

    def test_missing_required_field_rejected(self, client: TestClient) -> None:
        bad = _valid_session_start()
        del bad["adapter_name"]
        r = client.post("/sessions", json=bad)
        assert r.status_code == 422
        body = r.json()
        assert "errors" in body["detail"]
        assert any(
            "adapter_name" in err["message"] for err in body["detail"]["errors"]
        )

    def test_bad_session_id_format_rejected(self, client: TestClient) -> None:
        bad = _valid_session_start()
        bad["session_id"] = "not-a-hex-string"
        r = client.post("/sessions", json=bad)
        assert r.status_code == 422

    def test_non_object_body_rejected(self, client: TestClient) -> None:
        r = client.post("/sessions", json=["not", "an", "object"])
        assert r.status_code == 400


class TestEvent:
    def test_valid_event_after_session_start(self, client: TestClient) -> None:
        start = _valid_session_start()
        client.post("/sessions", json=start)
        r = client.post(
            f"/sessions/{start['session_id']}/events",
            json=_valid_event(str(start["session_id"])),
        )
        assert r.status_code == 202

    def test_event_path_id_must_match_body(self, client: TestClient) -> None:
        start = _valid_session_start()
        client.post("/sessions", json=start)
        body = _valid_event(str(start["session_id"]))
        r = client.post("/sessions/different-id/events", json=body)
        # Schema requires session_id matches the hex pattern; 422 first
        # because the body session_id != path. We just need a 4xx.
        assert 400 <= r.status_code < 500

    def test_event_for_unknown_session_accepted_as_orphan(
        self, client: TestClient
    ) -> None:
        # Skip session_start — exporter retries can arrive after a
        # backend restart that lost prior state. A real backend would
        # accept and tag the session as orphaned rather than 404.
        sid = "ffffffffffffffffffffffffffffffff"
        r = client.post(f"/sessions/{sid}/events", json=_valid_event(sid))
        assert r.status_code == 202


class TestSessionEnd:
    def test_full_lifecycle_round_trip(self, client: TestClient) -> None:
        start = _valid_session_start()
        sid = str(start["session_id"])
        client.post("/sessions", json=start)
        client.post(f"/sessions/{sid}/events", json=_valid_event(sid))
        r = client.post(f"/sessions/{sid}/end", json=_valid_session_end(sid))
        assert r.status_code == 202

        # The summary is now retrievable via the convenience endpoint.
        detail = client.get(f"/sessions/{sid}").json()
        assert detail["summary"]["events"] == 1
        assert detail["events"][0]["event_type"] == "user_prompt_submit"

    def test_summary_missing_required_field_rejected(self, client: TestClient) -> None:
        sid = "0000000000000000000000000000000a"
        bad = _valid_session_end(sid)
        del bad["summary"]
        r = client.post(f"/sessions/{sid}/end", json=bad)
        assert r.status_code == 422


class TestListSessions:
    def test_list_includes_started_session(self, client: TestClient) -> None:
        start = _valid_session_start()
        # Bump the id so we don't collide with prior tests' state — the
        # in-memory store lives for the lifetime of the FastAPI app.
        start["session_id"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        client.post("/sessions", json=start)
        r = client.get("/sessions")
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert any(s["session_id"] == start["session_id"] for s in sessions)
