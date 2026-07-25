"""Anonymous, shape-only usage telemetry with a stdlib-only PostHog sender.

Every event property is built in ``_telemetry_props.py`` (plus the environment
tags below) and documented in ``docs/telemetry.md``. Opt out with
``DO_NOT_TRACK=1`` or ``AGENTEGRITY_TELEMETRY_DISABLED=1``, or call
:func:`disable_telemetry` at runtime. Telemetry failures are never allowed to
reach the host process.
"""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import json
import os
import queue
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar, cast

# Public write-only project key: it can only ingest events, never read them.
PROJECT_API_KEY = "phc_CB2X8coGQ6k83TRRoyMcJnMRxbFr4QPzi4eD6BaAUNeo"
# US-hosted PostHog. Data-residency positioning note: an EU host
# (https://eu.i.posthog.com) would require a separate EU project.
HOST = "https://us.i.posthog.com"

_TRUTHY = {"1", "true", "yes", "on", "t", "y"}
_LINGER_SECONDS = 0.5
_FLUSH_DEADLINE_SECONDS = 2.0

_process_session_id = str(uuid.uuid4())

_disabled = False
_anonymous_id: str | None = None
_sender: _PosthogSender | None = None
_sender_lock = threading.Lock()

_scope_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "agentegrity_telemetry_scope_depth", default=0
)
_scope_tags: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "agentegrity_telemetry_scope_tags", default=None
)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Config / identity
# ---------------------------------------------------------------------------


def _env_truthy(name: str) -> bool:
    """Return True when the env var is set to a truthy opt-out value."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _should_disable() -> bool:
    """Return True when telemetry is disabled by env var or kill switch."""
    return _disabled or _env_truthy("DO_NOT_TRACK") or _env_truthy("AGENTEGRITY_TELEMETRY_DISABLED")


def _should_disable_geoip() -> bool:
    """Return True when geo enrichment is disabled but analytics kept."""
    return _env_truthy("AGENTEGRITY_TELEMETRY_DISABLE_GEOIP")


def disable_telemetry() -> None:
    """Disable telemetry for the rest of the process (runtime kill switch)."""
    global _disabled
    _disabled = True
    sender = _sender
    if sender is not None:
        sender.stop()


def _get_or_create_anonymous_id() -> str | None:
    """Return the persistent anonymous UUID, or None when telemetry is disabled."""
    global _anonymous_id
    if _should_disable():
        return None
    if _anonymous_id is not None:
        return _anonymous_id
    path = Path.home() / ".agentegrity" / "id"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new_id = str(uuid.uuid4())
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, new_id.encode())
            finally:
                os.close(fd)
            _anonymous_id = new_id
        except FileExistsError:
            # Lost the creation race (or the file already existed): use the winner's ID.
            _anonymous_id = path.read_text().strip() or f"anon-{uuid.uuid4()}"
    except OSError:
        # Read-only home, missing permissions, etc.: ephemeral per-process ID.
        _anonymous_id = f"anon-{uuid.uuid4()}"
    return _anonymous_id


def _get_environment_info() -> str:
    """Classify the runtime environment as a coarse enum string."""
    if os.environ.get("CI") or os.environ.get("TF_BUILD"):
        return "ci"
    if Path("/.dockerenv").exists():
        return "docker"
    if "google.colab" in sys.modules:
        return "colab"
    if os.environ.get("KAGGLE_KERNEL_RUN_TYPE"):
        return "kaggle"
    return "local"


def _environment_tags() -> dict[str, Any]:
    """Build the coarse environment tags attached to every event."""
    from agentegrity import __version__

    return {
        "agentegrity_version": __version__,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "environment": _get_environment_info(),
        "os_type": sys.platform,
    }


# ---------------------------------------------------------------------------
# Stdlib PostHog sender
# ---------------------------------------------------------------------------


class _PosthogSender:
    """Background batch sender; drops events rather than ever blocking or raising."""

    def __init__(self) -> None:
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=100)
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._stopped = False

    def enqueue(self, payload: dict[str, Any]) -> None:
        """Queue one event payload, dropping it when the queue is full."""
        if self._stopped:
            return
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            return
        self._ensure_thread()

    def stop(self) -> None:
        """Ask the worker to flush what it has and exit."""
        self._stopped = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._thread is None and not self._stopped:
                self._thread = threading.Thread(
                    target=self._worker, name="agentegrity-telemetry", daemon=True
                )
                self._thread.start()
                atexit.register(self._flush_at_exit)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            batch = [item]
            time.sleep(_LINGER_SECONDS)
            done = False
            while True:
                try:
                    extra = self._queue.get_nowait()
                except queue.Empty:
                    break
                if extra is None:
                    done = True
                    break
                batch.append(extra)
            self._post(batch)
            if done or self._stopped:
                return

    def _post(self, batch: list[dict[str, Any]]) -> None:
        try:
            body = json.dumps({"api_key": PROJECT_API_KEY, "batch": batch}).encode()
            request = urllib.request.Request(
                f"{HOST}/batch/",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=2):
                pass
        except Exception:
            pass

    def _flush_at_exit(self) -> None:
        try:
            self.stop()
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=_FLUSH_DEADLINE_SECONDS)
        except Exception:
            pass


def _get_sender() -> _PosthogSender:
    """Return the process-wide sender, creating it lazily."""
    global _sender
    if _sender is None:
        with _sender_lock:
            if _sender is None:
                _sender = _PosthogSender()
    return _sender


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def telemetry_capture(event: str, *, properties: dict[str, Any] | None = None) -> None:
    """Queue one anonymous event; dropped when disabled or outside a run context."""
    try:
        if _should_disable() or _scope_depth.get() == 0:
            return
        distinct_id = _get_or_create_anonymous_id()
        if distinct_id is None:
            return
        props: dict[str, Any] = _environment_tags()
        tags = _scope_tags.get()
        if tags:
            props.update(tags)
        if properties:
            props.update(properties)
        props["$session_id"] = _process_session_id
        if _should_disable_geoip():
            props["$geoip_disable"] = True
        _get_sender().enqueue(
            {
                "event": event,
                "distinct_id": distinct_id,
                "properties": props,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception:
        pass


@contextmanager
def telemetry_run_context() -> Iterator[None]:
    """Re-entrant telemetry scope; the outermost scope reports uncaught exceptions."""
    depth = _scope_depth.get()
    depth_token = _scope_depth.set(depth + 1)
    tags_token = _scope_tags.set({}) if depth == 0 else None
    try:
        yield
    except Exception as exc:
        if depth == 0:
            telemetry_capture(
                "agentegrity_uncaught_exception",
                properties={"exception_type": type(exc).__name__},
            )
        raise
    finally:
        _scope_depth.reset(depth_token)
        if tags_token is not None:
            _scope_tags.reset(tags_token)


def scoped_telemetry(func: F) -> F:
    """Wrap a sync or async callable in a telemetry run context."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with telemetry_run_context():
                return await func(*args, **kwargs)

        return cast(F, async_wrapper)

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with telemetry_run_context():
            return func(*args, **kwargs)

    return cast(F, wrapper)


def telemetry_tag(key: str, value: Any) -> None:
    """Attach a tag to every event captured within the current run context."""
    try:
        tags = _scope_tags.get()
        if tags is not None:
            tags[key] = value
    except Exception:
        pass
