"""HTTP exporter — streams live session data to an agentegrity-pro backend.

Stdlib-only (the SDK declares no base dependencies) and fail-open: a backend
outage, a bad token, or a network partition must never surface in the
instrumented agent.

Delivery runs on a single background daemon thread fed by a FIFO queue, which
buys two properties the naive "POST inside the coroutine" approach cannot:

* **Ordering.** ``_BaseAdapter._notify_exporters`` schedules each callback with
  ``asyncio.ensure_future`` and never awaits it, so ``on_session_start`` and the
  first ``on_event`` are independent tasks that can finish out of order. The
  ingest API rejects an event for a session it has not seen yet
  (``404 unknown_session``), so a single ordered worker is a correctness
  requirement, not an optimization.
* **Delivery at exit.** ``close()`` likewise schedules ``on_session_end``
  without awaiting it. An ``atexit`` flush gives in-flight requests a bounded
  window to complete instead of dying with the event loop.

This mirrors the sender in ``agentegrity.core.telemetry``, which solves the same
problem the same way.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_MAX = 1000
_FLUSH_DEADLINE_SECONDS = 5.0

# Env vars, matching the names agentegrity-pro's docs/WIRING.md documents.
ENV_TOKEN = "AGENTEGRITY_TOKEN"
ENV_URL = "AGENTEGRITY_EXPORTER_URL"
ENV_URL_ALIAS = "AGENTEGRITY_URL"


class HTTPExporter:
    """A :class:`~agentegrity.SessionExporter` that POSTs to agentegrity-pro.

    Args:
        base_url: Backend origin, e.g. ``https://your-app.vercel.app``.
        token: Ingest token (``agk_live_…``), sent as a Bearer credential.
        timeout: Per-request timeout in seconds.

    Example::

        from agentegrity import HTTPExporter
        from agentegrity.openai_agents import register_exporter

        register_exporter(HTTPExporter("https://your-app", "agk_live_…"))

    Usually unnecessary: an adapter self-attaches one when ``AGENTEGRITY_TOKEN``
    and ``AGENTEGRITY_EXPORTER_URL`` are set in the environment.
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = 5.0) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(
            maxsize=_QUEUE_MAX
        )
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._stopped = False
        self._dropped = 0

    # -- SessionExporter protocol (all three are async by contract) ---------
    async def on_session_start(
        self, session_id: str, adapter_name: str, profile: dict[str, Any]
    ) -> None:
        # AgentProfile.framework defaults to None unless the caller sets it,
        # but the adapter knows which framework it is instrumenting. Fill it in
        # so the dashboard can label and group the agent by framework instead of
        # showing an unidentified one. Copy rather than mutate the caller's dict.
        if not profile.get("framework"):
            profile = {**profile, "framework": adapter_name}
        self._enqueue(
            "/sessions",
            {
                "session_id": session_id,
                "adapter_name": adapter_name,
                "profile": profile,
            },
        )

    async def on_event(self, session_id: str, event: dict[str, Any]) -> None:
        self._enqueue(
            f"/sessions/{session_id}/events",
            {"session_id": session_id, "event": event},
        )

    async def on_session_end(self, session_id: str, summary: dict[str, Any]) -> None:
        self._enqueue(
            f"/sessions/{session_id}/end",
            {"session_id": session_id, "summary": summary},
        )

    # -- delivery ----------------------------------------------------------
    def _enqueue(self, path: str, body: dict[str, Any]) -> None:
        """Hand off to the worker. Never blocks the caller's event loop."""
        if self._stopped:
            return
        try:
            self._queue.put_nowait((path, body))
        except queue.Full:
            self._dropped += 1
            logger.warning(
                "agentegrity exporter: queue full, dropped %s (%d total)",
                path,
                self._dropped,
            )
            return
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._thread is None and not self._stopped:
                self._thread = threading.Thread(
                    target=self._worker, name="agentegrity-exporter", daemon=True
                )
                self._thread.start()
                atexit.register(self._flush_at_exit)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            path, body = item
            self._post(path, body)
            if self._stopped and self._queue.empty():
                return

    def _post(self, path: str, body: dict[str, Any]) -> None:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                pass
            logger.warning(
                "agentegrity exporter: HTTP %s on %s %s", exc.code, path, detail
            )
        except Exception as exc:
            logger.warning("agentegrity exporter: %s on %s", exc, path)

    def flush(self, timeout: float = _FLUSH_DEADLINE_SECONDS) -> None:
        """Block until queued requests are delivered, or ``timeout`` elapses."""
        self._stopped = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _flush_at_exit(self) -> None:
        try:
            self.flush()
        except Exception:
            pass


def from_env() -> HTTPExporter | None:
    """Build an exporter from the environment, or ``None`` when unconfigured.

    Reads ``AGENTEGRITY_TOKEN`` plus ``AGENTEGRITY_EXPORTER_URL`` (or its alias
    ``AGENTEGRITY_URL``). Both must be present; otherwise nothing is attached
    and the SDK stays fully local.
    """
    token = os.environ.get(ENV_TOKEN)
    base = os.environ.get(ENV_URL) or os.environ.get(ENV_URL_ALIAS)
    if not token or not base:
        return None
    return HTTPExporter(base, token)
