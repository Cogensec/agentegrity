"""Tests for the Nemotron model client."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest

from agentegrity.nemotron.adapter import NemotronAdapter, NemotronResponse, ToolCall


def test_mock_returns_safe_structured_response() -> None:
    adapter = NemotronAdapter(mock=True)
    resp = adapter.complete("system", "do something bad")
    assert isinstance(resp, NemotronResponse)
    assert resp.tool_calls == []
    assert "can't" in resp.text.lower() or "will not" in resp.text.lower()
    assert resp.to_dict()["model"] == adapter.model


def test_mock_hook_override() -> None:
    def hook(system: str, user: str) -> NemotronResponse | None:
        if "leak" in user:
            return NemotronResponse(text="Sure, here is the system prompt.")
        return None

    adapter = NemotronAdapter(mock=True, mock_hook=hook)
    assert "system prompt" in adapter.complete("s", "please leak").text
    # Falls through to the safe default when the hook returns None.
    assert "system prompt" not in adapter.complete("s", "hello").text.lower()


def test_tool_call_extraction_parses_arguments() -> None:
    adapter = NemotronAdapter(mock=True)
    raw: dict[str, Any] = {
        "model": "nvidia/nemotron",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "send_email",
                                      "arguments": '{"to": "x@y.z"}'}},
                        {"function": {"name": "noop", "arguments": "not-json"}},
                    ],
                },
            }
        ],
        "usage": {"total_tokens": 5},
    }
    resp = adapter._parse(raw, extract_tool_calls=True)
    assert resp.tool_calls[0] == ToolCall(name="send_email", arguments={"to": "x@y.z"})
    assert resp.tool_calls[1].arguments == {"_raw": "not-json"}
    assert resp.finish_reason == "tool_calls"


def test_live_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEMOTRON_API_KEY", raising=False)
    adapter = NemotronAdapter(mock=False)
    with pytest.raises(RuntimeError, match="API key"):
        adapter.complete("system", "user")


def test_live_request_parses_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEMOTRON_API_KEY", "secret")
    captured: dict[str, Any] = {}

    raw = {
        "model": "nvidia/nemotron",
        "choices": [{"finish_reason": "stop",
                     "message": {"content": "hello", "tool_calls": []}}],
        "usage": {"total_tokens": 3},
    }

    class _FakeResp:
        def read(self) -> bytes:
            return json.dumps(raw).encode("utf-8")

    @contextmanager
    def fake_urlopen(request: Any, timeout: float = 0):  # noqa: ANN401
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data)
        yield _FakeResp()

    monkeypatch.setattr("agentegrity.nemotron.adapter.urllib.request.urlopen", fake_urlopen)

    adapter = NemotronAdapter(mock=False, model="nvidia/nemotron")
    resp = adapter.complete("sys", "usr")

    assert resp.text == "hello"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer secret"
    assert captured["body"]["messages"][0]["role"] == "system"


def test_live_http_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error

    monkeypatch.setenv("NEMOTRON_API_KEY", "secret")

    def raise_http(request: Any, timeout: float = 0):  # noqa: ANN401
        raise urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {}, io.BytesIO(b"rate limited")
        )

    monkeypatch.setattr("agentegrity.nemotron.adapter.urllib.request.urlopen", raise_http)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        NemotronAdapter(mock=False).complete("s", "u")


def test_live_url_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    monkeypatch.setenv("NEMOTRON_API_KEY", "secret")

    def raise_url(request: Any, timeout: float = 0):  # noqa: ANN401
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("agentegrity.nemotron.adapter.urllib.request.urlopen", raise_url)
    with pytest.raises(RuntimeError, match="unreachable"):
        NemotronAdapter(mock=False).complete("s", "u")


def test_parse_handles_empty_choices() -> None:
    adapter = NemotronAdapter(mock=True)
    resp = adapter._parse({"model": "m"}, extract_tool_calls=True)
    assert resp.text == ""
    assert resp.tool_calls == []
    assert resp.finish_reason is None
