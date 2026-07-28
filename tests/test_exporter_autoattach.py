"""Env-driven exporter auto-attach.

This is what lets ``agentegrity pro … -- <command>`` stream an agent it did not
write: the adapter self-attaches an HTTP exporter when the environment supplies
a token and URL, so no user code changes. Absent those vars the SDK must stay
entirely local and send nothing anywhere.

Because two env vars are enough to turn on **full-content** egress (events carry
prompts and tool arguments, unlike the shape-only telemetry sender), attaching
must never be silent. It has to be discoverable two ways: an INFO log at attach
time, and an ``exporters`` entry in the session summary that ``report()``
returns. The log is the convenience; the summary is the durable surface, since
Python's default log level is WARNING and would swallow the INFO line entirely.
"""

from __future__ import annotations

import logging

from agentegrity import AgentegrityClient
from agentegrity.exporters.http import HTTPExporter


def _adapter():
    client = AgentegrityClient()
    profile = client.create_profile(name="t", agent_type="tool_using", risk_tier="low")
    return client.create_adapter("openai_agents", profile=profile)


def _clear(monkeypatch):
    for var in ("AGENTEGRITY_TOKEN", "AGENTEGRITY_EXPORTER_URL", "AGENTEGRITY_URL"):
        monkeypatch.delenv(var, raising=False)


def test_no_env_attaches_nothing(monkeypatch):
    _clear(monkeypatch)
    assert _adapter()._exporters == []


def test_token_without_url_attaches_nothing(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_x")
    assert _adapter()._exporters == []


def test_env_attaches_http_exporter(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_x")
    monkeypatch.setenv("AGENTEGRITY_EXPORTER_URL", "https://dash.test")

    exporters = _adapter()._exporters
    assert len(exporters) == 1
    assert isinstance(exporters[0], HTTPExporter)
    assert exporters[0].base == "https://dash.test"


def test_session_id_is_32_hex(monkeypatch):
    """The pro ingest schema requires ^[0-9a-f]{32}$; uuid4().hex satisfies it,
    so the exporter can forward the id untouched."""
    _clear(monkeypatch)
    sid = _adapter().session_id
    assert len(sid) == 32
    assert all(c in "0123456789abcdef" for c in sid)


# --- disclosure: attaching a network sink must never be silent ---


def test_attach_logs_the_destination(monkeypatch, caplog):
    _clear(monkeypatch)
    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_x")
    monkeypatch.setenv("AGENTEGRITY_EXPORTER_URL", "https://dash.test")

    with caplog.at_level(logging.INFO, logger="agentegrity.adapters"):
        _adapter()

    attached = [r for r in caplog.records if "streaming session data" in r.getMessage()]
    assert len(attached) == 1
    # Assert on the structured log args by equality rather than substring-matching
    # the formatted message. Equality is the stronger claim, and a URL substring
    # check is exactly the pattern CodeQL flags
    # (py/incomplete-url-substring-sanitization): a hostile URL can carry a
    # trusted-looking string at an arbitrary position, so `in` proves little.
    assert attached[0].args == ("https://dash.test", "AGENTEGRITY_EXPORTER_URL")


def test_attach_log_never_leaks_the_token(monkeypatch, caplog):
    """The URL is safe to print. The bearer token is not."""
    _clear(monkeypatch)
    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_supersecret")
    monkeypatch.setenv("AGENTEGRITY_EXPORTER_URL", "https://dash.test")

    with caplog.at_level(logging.DEBUG):
        _adapter()

    assert all("agk_live_supersecret" not in r.getMessage() for r in caplog.records)


def test_no_env_logs_nothing(monkeypatch, caplog):
    _clear(monkeypatch)
    with caplog.at_level(logging.INFO, logger="agentegrity.adapters"):
        _adapter()
    assert all("streaming session data" not in r.getMessage() for r in caplog.records)


def test_summary_reports_the_attached_sink(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AGENTEGRITY_TOKEN", "agk_live_x")
    monkeypatch.setenv("AGENTEGRITY_EXPORTER_URL", "https://dash.test")

    summary = _adapter().get_summary()
    assert summary["exporters"] == [
        {"type": "HTTPExporter", "target": "https://dash.test"}
    ]
    # The summary is serialized to the sink and to logs; it must stay token-free.
    assert "agk_live_x" not in str(summary)


def test_summary_reports_empty_when_local(monkeypatch):
    """A local-only run must be able to *prove* it is not streaming."""
    _clear(monkeypatch)
    assert _adapter().get_summary()["exporters"] == []


def test_exporter_without_describe_still_reported(monkeypatch):
    """describe() is an optional convention, not a Protocol requirement — a
    custom exporter predating it must not break the summary."""
    _clear(monkeypatch)

    class Custom:
        async def on_session_start(self, session_id, adapter_name, profile): ...
        async def on_event(self, session_id, event): ...
        async def on_session_end(self, session_id, summary): ...

    adapter = _adapter()
    adapter.register_exporter(Custom())
    assert adapter.get_summary()["exporters"] == [{"type": "Custom"}]
