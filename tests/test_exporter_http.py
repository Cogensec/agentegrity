"""HTTPExporter tests.

The exporter is the network sink that streams sessions to an agentegrity-pro
backend. Two properties matter beyond "it POSTs":

* **Ordering.** ``_notify_exporters`` fires callbacks as unawaited
  ``ensure_future`` tasks, so ``on_session_start`` and the first ``on_event``
  can complete out of order. The ingest API rejects an event for a session it
  has not seen (404), so delivery must be FIFO.
* **Fail-open.** A dead backend, a bad token, or a malformed response must
  never raise into the instrumented agent.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agentegrity.exporters.http import HTTPExporter, from_env


class _Recorder(HTTPServer):
    """Captures (path, body, auth) for every request, in arrival order."""

    def __init__(self, status: int = 202):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.received: list[tuple[str, dict, str | None]] = []
        self.status = status

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode() if length else "{}"
        self.server.received.append(
            (self.path, json.loads(raw), self.headers.get("Authorization"))
        )
        self.send_response(self.server.status)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass  # keep test output clean


@pytest.fixture
def server():
    srv = _Recorder()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


async def _run_session(exporter: HTTPExporter, sid: str = "a" * 32) -> None:
    await exporter.on_session_start(sid, "openai_agents", {"agent_id": "x", "name": "X"})
    await exporter.on_event(sid, {"event_type": "pre_tool_use", "data": {}})
    await exporter.on_session_end(sid, {"adapter": "openai_agents", "events": 1})


@pytest.mark.asyncio
async def test_posts_all_three_endpoints_in_order(server):
    exporter = HTTPExporter(server.base_url, "agk_live_test")
    await _run_session(exporter)
    exporter.flush()

    paths = [p for p, _, _ in server.received]
    assert paths == [
        "/sessions",
        f"/sessions/{'a' * 32}/events",
        f"/sessions/{'a' * 32}/end",
    ]


@pytest.mark.asyncio
async def test_sends_bearer_token(server):
    exporter = HTTPExporter(server.base_url, "agk_live_secret")
    await _run_session(exporter)
    exporter.flush()

    assert all(auth == "Bearer agk_live_secret" for _, _, auth in server.received)


@pytest.mark.asyncio
async def test_body_shapes_match_the_wire_contract(server):
    exporter = HTTPExporter(server.base_url, "t")
    await _run_session(exporter)
    exporter.flush()

    start, event, end = (body for _, body, _ in server.received)
    assert set(start) == {"session_id", "adapter_name", "profile"}
    assert set(event) == {"session_id", "event"}
    assert set(end) == {"session_id", "summary"}


@pytest.mark.asyncio
async def test_fills_framework_from_adapter_name(server):
    """AgentProfile.framework defaults to None; the dashboard needs it set."""
    exporter = HTTPExporter(server.base_url, "t")
    profile = {"agent_id": "x", "framework": None}
    await exporter.on_session_start("b" * 32, "crewai", profile)
    exporter.flush()

    _, body, _ = server.received[0]
    assert body["profile"]["framework"] == "crewai"
    assert profile["framework"] is None  # caller's dict not mutated


@pytest.mark.asyncio
async def test_explicit_framework_is_preserved(server):
    exporter = HTTPExporter(server.base_url, "t")
    await exporter.on_session_start("c" * 32, "crewai", {"framework": "custom"})
    exporter.flush()

    _, body, _ = server.received[0]
    assert body["profile"]["framework"] == "custom"


@pytest.mark.asyncio
async def test_unreachable_backend_never_raises():
    exporter = HTTPExporter("http://127.0.0.1:1", "t", timeout=0.5)
    await _run_session(exporter)  # must not raise
    exporter.flush(timeout=3)


@pytest.mark.asyncio
async def test_rejected_token_never_raises():
    srv = _Recorder(status=401)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        exporter = HTTPExporter(srv.base_url, "bad")
        await _run_session(exporter)  # 401 on every call, still no raise
        exporter.flush()
    finally:
        srv.shutdown()
        srv.server_close()


def test_from_env_requires_both_vars(monkeypatch):
    monkeypatch.delenv("AGENTEGRITY_TOKEN", raising=False)
    monkeypatch.delenv("AGENTEGRITY_EXPORTER_URL", raising=False)
    monkeypatch.delenv("AGENTEGRITY_URL", raising=False)
    assert from_env() is None

    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_x")
    assert from_env() is None  # token alone is not enough

    monkeypatch.setenv("AGENTEGRITY_EXPORTER_URL", "https://example.test/")
    exporter = from_env()
    assert exporter is not None
    assert exporter.base == "https://example.test"  # trailing slash trimmed
    assert exporter.token == "agk_live_x"


def test_from_env_accepts_url_alias(monkeypatch):
    monkeypatch.delenv("AGENTEGRITY_EXPORTER_URL", raising=False)
    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_x")
    monkeypatch.setenv("AGENTEGRITY_URL", "https://alias.test")
    exporter = from_env()
    assert exporter is not None and exporter.base == "https://alias.test"
