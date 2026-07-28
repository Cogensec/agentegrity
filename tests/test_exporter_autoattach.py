"""Env-driven exporter auto-attach.

This is what lets ``agentegrity pro … -- <command>`` stream an agent it did not
write: the adapter self-attaches an HTTP exporter when the environment supplies
a token and URL, so no user code changes. Absent those vars the SDK must stay
entirely local and send nothing anywhere.
"""

from __future__ import annotations

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
