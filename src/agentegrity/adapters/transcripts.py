"""Token usage read from coding-agent transcripts.

Claude Code and Codex hooks carry no usage, but every hook payload names
the session's transcript (``transcript_path``) and ``SubagentStop`` names
the child's (``agent_transcript_path``). These readers pull the usage
lines out of those JSONL files into a :class:`UsageLedger`. Only usage
fields and model names are read; nothing else in a transcript leaves it.

Each file is read from where the last read stopped. A file that was
replaced or truncated is read again from the start, which is harmless
because the ledger dedupes by request id.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from agentegrity.core.usage import TokenUsage, UsageLedger

SOURCE = "transcript"
# A replaced file can reuse the inode, so its first line identifies it too.
_HEAD_BYTES = 65536
_PATH_KEYS = ("transcript_path", "agent_transcript_path")


class _JsonlTail:
    """Complete lines appended to a JSONL file since the last read.

    It also remembers the last call it counted. When the file is replaced
    (a cloud session resumes from a transcript rebuilt after compaction),
    lines up to and including that call were counted before and are
    skipped; a replacement that does not contain it is counted whole.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fresh = True
        self._offset = 0
        self._identity: tuple[int, int] | None = None
        self._head: str | None = None
        self._last_call: str | None = None
        self._skip_until: str | None = None
        self._passed = False

    def read(self) -> Iterator[bytes]:
        """Yield new complete lines; a line still being written waits for the next read."""
        try:
            stat = self.path.stat()
        except OSError:
            return
        identity, head = (stat.st_dev, stat.st_ino), self._read_head()
        if identity != self._identity or head != self._head or stat.st_size < self._offset:
            if self._identity is not None and self._last_call and self._contains(self._last_call):
                self._skip_until, self._passed = self._last_call, False
            self._identity, self._head, self._offset = identity, head, 0
        try:
            with self.path.open("rb") as fh:
                fh.seek(self._offset)
                for line in fh:
                    if not line.endswith(b"\n"):
                        break
                    self._offset += len(line)
                    yield line
        except OSError:
            return
        finally:
            self.fresh = False

    def claim(self, call: str) -> bool:
        """True when this call is new and should be counted; it becomes the last counted."""
        if self._skip_until is not None:
            if call == self._skip_until:
                self._passed = True
                return False
            if not self._passed:
                return False
            self._skip_until = None
        self._last_call = call
        return True

    def _read_head(self) -> str | None:
        try:
            with self.path.open("rb") as fh:
                return hashlib.sha256(fh.readline(_HEAD_BYTES)).hexdigest()
        except OSError:
            return None

    def _contains(self, call: str) -> bool:
        try:
            return f'"{call}"'.encode() in self.path.read_bytes()
        except OSError:
            return False

    def state(self) -> dict[str, Any]:
        """Where reading stopped, to resume after a restart."""
        return {
            "identity": self._identity, "head": self._head,
            "offset": self._offset, "last_call": self._last_call,
        }

    def restore(self, state: Mapping[str, Any]) -> None:
        """Resume from :meth:`state`; malformed values leave the tail reading from the start."""
        identity, offset, call = state.get("identity"), state.get("offset"), state.get("last_call")
        head = state.get("head")
        if (
            isinstance(identity, list | tuple) and len(identity) == 2
            and all(isinstance(i, int) for i in identity)
            and isinstance(offset, int) and offset >= 0 and isinstance(head, str)
        ):
            self._identity, self._head = (identity[0], identity[1]), head
            self._offset, self.fresh = offset, False
            self._last_call = call if isinstance(call, str) else None


class TranscriptUsage:
    """Feed a ledger from the transcripts named in hook payloads."""

    def __init__(self, ledger: UsageLedger) -> None:
        self._ledger = ledger
        self._tails: dict[str, _JsonlTail] = {}

    def note(self, data: Mapping[str, Any]) -> None:
        """Remember the transcripts a hook payload names."""
        for key in _PATH_KEYS:
            path = data.get(key)
            if isinstance(path, str) and path and path not in self._tails:
                self._tails[path] = _JsonlTail(Path(path))

    def refresh(self) -> None:
        """Read every known transcript's new lines into the ledger."""
        for tail in self._tails.values():
            self._consume(tail)

    def state(self) -> dict[str, Any]:
        """Known transcripts and where each was read to, as JSON-ready data."""
        return {"tails": {path: tail.state() for path, tail in self._tails.items()}}

    def restore(self, state: Any) -> None:
        """Resume from :meth:`state` after a restart; anything malformed is ignored."""
        tails = state.get("tails") if isinstance(state, Mapping) else None
        if not isinstance(tails, Mapping):
            return
        for path, tail_state in tails.items():
            if isinstance(path, str) and path and isinstance(tail_state, Mapping):
                tail = self._tails.setdefault(path, _JsonlTail(Path(path)))
                tail.restore(tail_state)

    def _consume(self, tail: _JsonlTail) -> None:
        raise NotImplementedError


class ClaudeTranscriptUsage(TranscriptUsage):
    """Claude Code transcripts: one line per content block, keyed by ``requestId``.

    Anthropic counts cached tokens outside ``input_tokens``, so they are
    added back. A request's early lines carry a placeholder output count
    and no ``stop_reason``; the last line wins, and a request that never
    got its final line stays marked incomplete.
    """

    def _consume(self, tail: _JsonlTail) -> None:
        first = tail.fresh
        for line in tail.read():
            if first and b'"compact_boundary"' in line:
                # The file starts after a compaction: usage before it is gone.
                self._ledger.mark_incomplete()
            first = False
            if b'"usage"' in line:
                self._record(tail, _parse(line))

    def _record(self, tail: _JsonlTail, line: Mapping[str, Any]) -> None:
        message = line.get("message")
        if line.get("type") != "assistant" or not isinstance(message, Mapping):
            return
        usage = message.get("usage")
        key = line.get("requestId") or message.get("id")
        model = message.get("model")
        if not isinstance(usage, Mapping) or not key or model == "<synthetic>":
            return
        if not tail.claim(str(key)):
            return
        cache_read = _int(usage, "cache_read_input_tokens")
        cache_write = _int(usage, "cache_creation_input_tokens")
        self._ledger.record(
            f"claude:{key}",
            model if isinstance(model, str) else None,
            TokenUsage(
                input_tokens=_int(usage, "input_tokens") + cache_read + cache_write,
                output_tokens=_int(usage, "output_tokens"),
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
            ),
            source=SOURCE,
            complete=message.get("stop_reason") is not None,
        )


class CodexRolloutUsage(TranscriptUsage):
    """Codex rollouts: one ``token_usage_record`` per response, keyed by ``response_id``.

    Builds that predate the record only write ``token_count`` events. Their
    ``total_token_usage`` is a running total that a context overflow
    overwrites and a fork copies into the child, so the fallback counts
    each distinct total once, skips overflow snapshots, and is marked
    incomplete.
    """

    def __init__(self, ledger: UsageLedger) -> None:
        super().__init__(ledger)
        self._hook_model: str | None = None
        self._models: dict[Path, str] = {}
        self._has_records: set[Path] = set()

    def note(self, data: Mapping[str, Any]) -> None:
        super().note(data)
        model = data.get("model")
        if isinstance(model, str) and model:
            self._hook_model = model

    def _consume(self, tail: _JsonlTail) -> None:
        fallback: list[Mapping[str, Any]] = []
        for raw in tail.read():
            if not any(k in raw for k in (b"token_usage_record", b"turn_context", b"token_count")):
                continue
            line = _parse(raw)
            payload = line.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if line.get("type") == "turn_context" and isinstance(payload.get("model"), str):
                self._models[tail.path] = payload["model"]
            elif line.get("type") == "token_usage_record":
                self._has_records.add(tail.path)
                self._record_response(tail, payload)
            elif payload.get("type") == "token_count":
                fallback.append(payload)
        if tail.path not in self._has_records:
            for payload in fallback:
                self._record_snapshot(tail.path, payload)

    def _record_response(self, tail: _JsonlTail, payload: Mapping[str, Any]) -> None:
        usage = payload.get("usage")
        response = payload.get("response_id")
        if isinstance(usage, Mapping) and response and tail.claim(str(response)):
            self._ledger.record(f"codex:{response}", self._model(tail.path), _codex(usage),
                                source=SOURCE)

    def state(self) -> dict[str, Any]:
        return {
            **super().state(),
            "models": {str(path): model for path, model in self._models.items()},
            "has_records": sorted(str(path) for path in self._has_records),
        }

    def restore(self, state: Any) -> None:
        super().restore(state)
        if not isinstance(state, Mapping):
            return
        models, has_records = state.get("models"), state.get("has_records")
        if isinstance(models, Mapping):
            self._models.update(
                {Path(p): m for p, m in models.items() if isinstance(p, str) and isinstance(m, str)}
            )
        if isinstance(has_records, list):
            self._has_records.update(Path(p) for p in has_records if isinstance(p, str))

    def _record_snapshot(self, path: Path, payload: Mapping[str, Any]) -> None:
        info = payload.get("info")
        if not isinstance(info, Mapping):
            return
        total, last = info.get("total_token_usage"), info.get("last_token_usage")
        if not isinstance(total, Mapping) or not isinstance(last, Mapping):
            return
        if not _int(total, "input_tokens") and not _int(total, "output_tokens"):
            return  # a context-overflow fill, not a response
        key = "codex-count:" + ":".join(
            str(_int(total, k)) for k in ("total_tokens", "input_tokens", "output_tokens")
        )
        self._ledger.record(key, self._model(path), _codex(last), source=SOURCE, complete=False)

    def _model(self, path: Path) -> str | None:
        return self._models.get(path) or self._hook_model


def _codex(usage: Mapping[str, Any]) -> TokenUsage:
    """Codex already counts cached tokens inside ``input_tokens``."""
    return TokenUsage(
        input_tokens=_int(usage, "input_tokens"),
        output_tokens=_int(usage, "output_tokens"),
        cache_read_tokens=_int(usage, "cached_input_tokens"),
        cache_write_tokens=_optional_int(usage, "cache_write_input_tokens"),
        reasoning_tokens=_int(usage, "reasoning_output_tokens"),
    )


def _parse(line: bytes) -> Mapping[str, Any]:
    try:
        parsed = json.loads(line)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _int(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_int(source: Mapping[str, Any], key: str) -> int | None:
    return _int(source, key) if key in source else None
