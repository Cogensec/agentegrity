"""Token usage per session, normalized across frameworks.

Frameworks disagree on what "input tokens" means: Anthropic leaves cached
tokens out, OpenAI, Gemini and LangChain count them in. Every adapter
records one shape instead:

* ``input_tokens`` is every input token processed, cached or not.
  ``cache_read_tokens`` and ``cache_write_tokens`` are the parts of it read
  from or written to a prompt cache.
* ``output_tokens`` is every generated token; ``reasoning_tokens`` is the
  part spent on reasoning.

The breakdown fields are ``None`` when a source does not report them, and
stay out of the totals until some source does, so a missing number is
never shown as zero.

Entries are keyed per model call (a request or response id), and a
repeated key replaces the earlier entry. Sources that write the same call
more than once (transcripts rewrite a request as it streams) or report a
running total (a cumulative snapshot) are deduplicated by the key, not by
the reader.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields
from typing import Any

_BREAKDOWN = ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "requests")
UNKNOWN_MODEL = "unknown"


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for one model call, or one running snapshot of several."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    requests: int | None = 1

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            **{name: _add(getattr(self, name), getattr(other, name)) for name in _BREAKDOWN},
        )

    def to_dict(self) -> dict[str, int]:
        """Serialize, leaving out breakdowns no source reported."""
        out = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }
        out["total_tokens"] = self.input_tokens + self.output_tokens
        return out


_ZERO = TokenUsage(input_tokens=0, output_tokens=0, requests=None)


def _add(left: int | None, right: int | None) -> int | None:
    """Sum two optional counts; None only when neither side reported one."""
    if left is None:
        return right
    if right is None:
        return left
    return left + right


@dataclass(frozen=True)
class _Entry:
    model: str
    usage: TokenUsage
    source: str
    complete: bool


class UsageLedger:
    """Running per-model token totals for one session, safe across threads."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._gap = False
        self._lock = threading.Lock()

    def record(self,
        key: str,
        model: str | None,
        usage: TokenUsage,
        *,
        source: str,
        complete: bool = True,
    ) -> None:
        """Record one call's usage; a key seen before is replaced, not added."""
        with self._lock:
            self._entries[key] = _Entry(model or UNKNOWN_MODEL, usage, source, complete)

    def discard(self, source: str) -> None:
        """Drop every entry from one source, when a more exact source supersedes it."""
        with self._lock:
            self._entries = {k: e for k, e in self._entries.items() if e.source != source}

    def mark_incomplete(self) -> None:
        """Flag a known gap: calls happened that no source can account for."""
        self._gap = True

    def to_dict(self) -> dict[str, Any] | None:
        """Totals overall and per model, or None when nothing was recorded."""
        with self._lock:
            entries = list(self._entries.values())
        if not entries:
            return None
        by_model: dict[str, TokenUsage] = {}
        for entry in entries:
            by_model[entry.model] = by_model.get(entry.model, _ZERO) + entry.usage
        total = sum(by_model.values(), _ZERO)
        return {
            **total.to_dict(),
            "complete": not self._gap and all(e.complete for e in entries),
            "sources": sorted({e.source for e in entries}),
            "by_model": {model: usage.to_dict() for model, usage in sorted(by_model.items())},
        }
