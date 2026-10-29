"""Canonical Pro delivery tests: no live network, tokens or customer data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError

import pytest

from agentegrity.core.agent_events import (
    CanonicalAgentEvent,
    EventSource,
    SourceAssurance,
)
from agentegrity.core.host_observer import HostAgentObserver, ToolCompletion
from agentegrity.exporters import canonical_pro
from agentegrity.exporters.canonical_pro import (
    CanonicalDeliveryDuplicate,
    CanonicalDeliveryRejected,
    CanonicalDeliveryUncertain,
    CanonicalProSender,
)

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
ENDPOINT = "https://staging.example.test/canonical/events"
TOKEN = "synthetic-test-token-secret"


def _event(*, event_id: str = "event-1", event_type: str = "tool.intent"):
    return CanonicalAgentEvent(
        event_id=event_id, event_type=event_type, occurred_at=NOW,
        agent_id="agent-a", run_id="run-a",
        source=EventSource("trusted-host", "host-sdk", SourceAssurance.HOST_ASSERTED),
        tool_name="prepare_transfer", effect="financial_write",
    )


def _ack(event_id: str) -> bytes:
    return json.dumps({
        "ok": True, "event_id": event_id,
        "source_assurance_effective": "unverified",
    }).encode("utf-8")


def test_observer_to_sender_acceptance_is_metadata_only() -> None:
    posts: list[dict[str, Any]] = []

    def fake(url: str, body: bytes, headers: Any, timeout: float):
        posts.append({
            "url": url, "body": json.loads(body), "headers": dict(headers),
            "timeout": timeout,
        })
        return 202, _ack(json.loads(body)["event_id"])

    observer = HostAgentObserver(
        agent_id="bank-agent", run_id="bank-run",
        host="custom-banking-host", integration="python",
    )
    observer.start(when=NOW)
    observer.tool_intent(
        action_id="host-action", tool_name="prepare_transfer",
        effect="financial_write", when=NOW,
    )
    observer.tool_outcome(
        action_id="host-action", completion=ToolCompletion.RETURNED, when=NOW
    )
    observer.finish(when=NOW)
    sender = CanonicalProSender(endpoint=ENDPOINT, token=TOKEN, transport=fake)
    # Construction and host observation never initiate network activity.
    assert posts == []

    acknowledgements = [sender.send(event) for event in observer.events]
    assert len(posts) == 4
    assert len(acknowledgements) == 4
    assert all(a.source_assurance_effective == "unverified" for a in acknowledgements)
    assert all(p["headers"]["Authorization"] == "Bearer " + TOKEN for p in posts)
    assert all(p["headers"]["Content-Type"] == "application/json" for p in posts)
    assert all(p["url"] == ENDPOINT for p in posts)
    assert all("prompt" not in p["body"] for p in posts)
    assert all("arguments" not in p["body"] for p in posts)
    assert all("customer_data" not in p["body"] for p in posts)
    assert posts[1]["body"]["event_type"] == "tool.intent"
    assert posts[2]["body"]["outcome"] == "returned_unverified"
    assert "Authorization" not in str(posts[0]["body"])


@pytest.mark.parametrize("endpoint", [
    "http://staging.example.test/canonical/events",
    "https://evil.example.test/other",
    "https://user:pass@staging.example.test/canonical/events",
    "https://staging.example.test/canonical/events?token=x",
    "https://staging.example.test/canonical/events#secret",
    "https://staging.example.test/canonical/events/",
    "https://staging.example.test/canonical/events?redirect=https://elsewhere",
])
def test_sender_rejects_insecure_or_unexpected_endpoints(endpoint: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        CanonicalProSender(endpoint=endpoint, token=TOKEN)


@pytest.mark.parametrize("token", [
    "", "  ", "secret" + chr(10) + "header: x", "secret" + chr(13) + "header: x",
    "hi" + chr(0) + "there",
])
def test_sender_rejects_unusable_tokens(token: str) -> None:
    with pytest.raises(ValueError, match="token"):
        CanonicalProSender(endpoint=ENDPOINT, token=token)


@pytest.mark.parametrize("timeout", [-1, 0, 31, True, float("nan")])
def test_sender_rejects_bad_timeouts(timeout: Any) -> None:
    with pytest.raises(ValueError, match="timeout"):
        CanonicalProSender(endpoint=ENDPOINT, token=TOKEN, timeout=timeout)


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_permanent_rejections_are_not_retried(status: int) -> None:
    calls: list[int] = []

    def fake(url: str, content: bytes, headers: Any, timeout: float):
        calls.append(1)
        return status, b"private upstream details"

    sender = CanonicalProSender(endpoint=ENDPOINT, token=TOKEN, transport=fake)
    with pytest.raises(CanonicalDeliveryRejected) as exc:
        sender.send(_event())
    assert exc.value.status_code == status
    assert len(calls) == 1
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_ambiguous_statuses_raise_uncertain_without_retries(status: int) -> None:
    calls = []

    def fake(url: str, content: bytes, headers: Any, timeout: float):
        calls.append(1)
        return status, b""

    sender = CanonicalProSender(endpoint=ENDPOINT, token=TOKEN, transport=fake)
    with pytest.raises(CanonicalDeliveryUncertain) as exc:
        sender.send(_event())
    assert exc.value.status_code == status
    assert len(calls) == 1


def test_duplicate_is_not_treated_as_success() -> None:
    sender = CanonicalProSender(
        endpoint=ENDPOINT, token=TOKEN,
        transport=lambda *_args: (409, b""),
    )
    with pytest.raises(CanonicalDeliveryDuplicate) as exc:
        sender.send(_event())
    assert exc.value.status_code == 409


@pytest.mark.parametrize("body", [
    b"",
    b"not json",
    b"[]",
    b'{"ok":false,"event_id":"event-1","source_assurance_effective":"unverified"}',
    b'{"ok":true,"event_id":"another","source_assurance_effective":"unverified"}',
    b'{"ok":true,"event_id":"event-1","source_assurance_effective":"authenticated"}',
    b"x" * 4100,
])
def test_bad_or_mismatched_accepted_receipts_are_uncertain(body: bytes) -> None:
    sender = CanonicalProSender(
        endpoint=ENDPOINT, token=TOKEN,
        transport=lambda *_args: (202, body),
    )
    with pytest.raises(CanonicalDeliveryUncertain):
        sender.send(_event())


def test_timeout_may_follow_commit_and_never_logs_secrets() -> None:
    def fake(url: str, content: bytes, headers: Any, timeout: float):
        raise RuntimeError("sensitive " + TOKEN + " " + url)

    sender = CanonicalProSender(endpoint=ENDPOINT, token=TOKEN, transport=fake)
    with pytest.raises(CanonicalDeliveryUncertain) as exc:
        sender.send(_event())
    assert TOKEN not in str(exc.value)
    assert ENDPOINT not in str(exc.value)
    assert exc.value.__cause__ is None


def test_oversized_metadata_is_not_sent() -> None:
    calls: list[int] = []

    def fake(url: str, content: bytes, headers: Any, timeout: float):
        calls.append(1)
        return 202, _ack("event-1")

    sender = CanonicalProSender(endpoint=ENDPOINT, token=TOKEN, transport=fake)
    with pytest.raises(ValueError, match="8 KiB"):
        sender.send(_event(event_type="tool." + ("x" * 9000)))
    assert calls == []


def test_only_canonical_event_objects_are_admitted() -> None:
    sender = CanonicalProSender(
        endpoint=ENDPOINT, token=TOKEN, transport=lambda *_args: (202, _ack("event-1")),
    )
    with pytest.raises(TypeError, match="CanonicalAgentEvent"):
        sender.send({"raw_prompt": "secret"})  # type: ignore[arg-type]


def test_https_transport_disables_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    redirects: list[str] = []

    class FakeOpener:
        def open(self, req: Any, timeout: float) -> Any:
            redirects.append(req.full_url)
            raise HTTPError(req.full_url, 307, "redirect", {}, None)

    def build(handler: Any) -> Any:
        assert isinstance(handler, canonical_pro._NoRedirect)
        return FakeOpener()

    monkeypatch.setattr(canonical_pro, "build_opener", build)
    status, body = canonical_pro._https_post(
        ENDPOINT, b"{}", {"Authorization": "Bearer " + TOKEN}, 5.0
    )
    assert (status, body) == (307, b"")
    assert redirects == [ENDPOINT]
