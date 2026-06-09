"""Nemotron model client.

A standalone model adapter (not a framework hook adapter) that drives a
Nemotron model in two modes:

* **mock** — deterministic, offline, safe responses. Used for reproducible
  benchmarking and CI. A per-call override lets tests inject a *compromised*
  response and assert the benchmark fails it.
* **live** — POSTs an OpenAI-compatible ``/chat/completions`` request to a
  configurable endpoint using only the standard library (``urllib``). The API
  key is read from a configurable environment variable.

The adapter returns a :class:`NemotronResponse`: a structured object carrying
the model text, any extracted tool calls, and the raw payload.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_ENDPOINT = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-4-340b-instruct"
DEFAULT_API_KEY_ENV = "NEMOTRON_API_KEY"

# Deterministic mock response: a clear refusal that contains no disallowed
# content and emits no tool call. Every benchmark case that wants safe
# behaviour passes against this baseline.
_MOCK_REFUSAL = (
    "I can't comply with that request. It conflicts with my operating "
    "instructions, so I will not proceed. Escalating to a human reviewer "
    "for oversight before any further action is taken."
)


@dataclass
class ToolCall:
    """A single tool/function call extracted from a model response."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


@dataclass
class NemotronResponse:
    """Structured Nemotron completion."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "model": self.model,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
        }


# A mock hook lets a test substitute the canned response for a specific
# (system_prompt, user_prompt) pair. Returns None to fall through to the
# default safe refusal.
MockHook = Callable[[str, str], NemotronResponse | None]


class NemotronAdapter:
    """Client for a Nemotron model endpoint.

    Parameters
    ----------
    model : str
        Model identifier passed through to the endpoint.
    endpoint : str
        OpenAI-compatible base URL (``/chat/completions`` is appended).
    api_key_env : str
        Environment variable holding the API key (live mode only).
    mock : bool
        When True, never touches the network; returns deterministic safe
        responses (optionally overridden by ``mock_hook``).
    timeout : float
        Live-request timeout in seconds.
    mock_hook : callable, optional
        ``(system_prompt, user_prompt) -> NemotronResponse | None``. When it
        returns a response, that response is used; when it returns None the
        default safe refusal is returned. Ignored in live mode.
    """

    def __init__(self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        mock: bool = False,
        timeout: float = 30.0,
        mock_hook: MockHook | None = None,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.api_key_env = api_key_env
        self.mock = mock
        self.timeout = timeout
        self._mock_hook = mock_hook

    @property
    def name(self) -> str:
        return "nemotron"

    def complete(self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        extract_tool_calls: bool = True,
    ) -> NemotronResponse:
        """Run a single completion and return a structured response."""
        if self.mock:
            return self._mock_complete(system_prompt, user_prompt)
        return self._live_complete(
            system_prompt, user_prompt, tools, extract_tool_calls
        )

    # -- mock --------------------------------------------------------------

    def _mock_complete(self, system_prompt: str, user_prompt: str) -> NemotronResponse:
        if self._mock_hook is not None:
            override = self._mock_hook(system_prompt, user_prompt)
            if override is not None:
                return override
        return NemotronResponse(
            text=_MOCK_REFUSAL,
            tool_calls=[],
            model=self.model,
            finish_reason="stop",
            usage={"mock": True},
            raw={"mock": True},
        )

    # -- live --------------------------------------------------------------

    def _live_complete(self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]] | None,
        extract_tool_calls: bool,
    ) -> NemotronResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Nemotron live mode requires an API key in ${self.api_key_env}. "
                f"Set the environment variable or run with mock=True."
            )

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if tools:
            body["tools"] = tools

        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(
                f"Nemotron endpoint returned HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Nemotron endpoint unreachable: {exc.reason}") from exc

        return self._parse(raw, extract_tool_calls)

    def _parse(self, raw: dict[str, Any], extract_tool_calls: bool) -> NemotronResponse:
        choices = raw.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        finish_reason = choices[0].get("finish_reason") if choices else None

        tool_calls: list[ToolCall] = []
        if extract_tool_calls:
            tool_calls = self._extract_tool_calls(message.get("tool_calls") or [])

        return NemotronResponse(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            model=raw.get("model", self.model),
            finish_reason=finish_reason,
            usage=raw.get("usage", {}),
            raw=raw,
        )

    @staticmethod
    def _extract_tool_calls(entries: list[dict[str, Any]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for entry in entries:
            fn = entry.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            arguments = fn.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            calls.append(ToolCall(name=name, arguments=arguments))
        return calls


__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "NemotronAdapter",
    "NemotronResponse",
    "ToolCall",
]
