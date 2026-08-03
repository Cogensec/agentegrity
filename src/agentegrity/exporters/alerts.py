"""Webhook alert exporters — push block/escalate verdicts to chat.

:class:`WebhookAlertExporter` implements the :class:`SessionExporter`
protocol and POSTs a small JSON alert whenever an evaluated event's
action reaches the configured severity (default: ``block`` and
``escalate``). :class:`SlackAlertExporter` is the same exporter with
the payload shaped for Slack incoming webhooks (``{"text": ...}``,
which Discord- and Teams-compatible endpoints also accept).

Register on any adapter::

    adapter.register_exporter(SlackAlertExporter(os.environ["SLACK_WEBHOOK_URL"]))

Stdlib-only and fail-open like every exporter: delivery runs on a
single background daemon thread (alerts are low-volume; ordering
within a session is preserved), errors are logged and swallowed, and
``on_session_end`` flushes with a bounded deadline. The alert payload
carries verdict shape only — action, scores, layer names, event type,
tool name — never prompts or tool arguments.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.parse
import urllib.request
from typing import Any

from agentegrity.adapters.base import FrameworkEvent

logger = logging.getLogger("agentegrity.exporters.alerts")

_QUEUE_MAX = 200
_FLUSH_DEADLINE_SECONDS = 5.0
_DEFAULT_ALERT_ACTIONS = frozenset({"block", "escalate"})


class WebhookAlertExporter:
    """POST a JSON alert for every block/escalate verdict.

    Parameters
    ----------
    url : str
        Webhook endpoint. POSTed with ``Content-Type: application/json``.
    alert_on : set[str], optional
        Actions that fire an alert. Default ``{"block", "escalate"}``.
    timeout : float
        Per-request timeout in seconds. Default 5.0.
    """

    def __init__(
        self,
        url: str,
        *,
        alert_on: set[str] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._url = url
        self._alert_on = (
            frozenset(alert_on) if alert_on is not None else _DEFAULT_ALERT_ACTIONS
        )
        self._timeout = timeout
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=_QUEUE_MAX
        )
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- SessionExporter protocol ---

    def on_session_start(
        self, session_id: str, adapter_name: str, profile: dict[str, Any]
    ) -> None:
        return None

    def on_event(self, session_id: str, event: FrameworkEvent) -> None:
        score = event.evaluation_result
        if score is None or score.action not in self._alert_on:
            return
        alert = {
            "source": "agentegrity",
            "session_id": session_id,
            "adapter": event.adapter_name,
            "event_type": event.event_type,
            "action": score.action,
            "composite": round(score.composite, 4),
            "failing_layers": [
                r.layer_name for r in score.layer_results if not r.passed
            ],
            "tool_name": event.data.get("tool_name"),
            "timestamp": event.timestamp.isoformat(),
        }
        try:
            self._queue.put_nowait(alert)
        except queue.Full:
            logger.warning("alert queue full; dropping %s alert", score.action)
            return
        self._ensure_worker()

    def on_session_end(self, session_id: str, summary: dict[str, Any]) -> None:
        self.flush()

    # --- delivery ---

    def describe(self) -> dict[str, str]:
        """Disclosure hook — reported by ``get_summary()["exporters"]``.
        The query string is stripped so webhook tokens never surface."""
        parts = urllib.parse.urlsplit(self._url)
        return {
            "type": type(self).__name__,
            "target": f"{parts.scheme}://{parts.netloc}{parts.path}",
        }

    def flush(self, timeout: float = _FLUSH_DEADLINE_SECONDS) -> None:
        """Block until queued alerts are delivered (bounded)."""
        worker = self._worker
        if worker is None or not worker.is_alive():
            # No live worker — drain synchronously so nothing is lost.
            while True:
                try:
                    alert = self._queue.get_nowait()
                except queue.Empty:
                    return
                if alert is not None:
                    self._post(alert)
        done = threading.Event()
        self._queue.put({"__flush__": done})
        done.wait(timeout)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, name="agentegrity-alerts", daemon=True
                )
                self._worker.start()

    def _run(self) -> None:
        while True:
            alert = self._queue.get()
            if alert is None:
                return
            marker = alert.get("__flush__")
            if isinstance(marker, threading.Event):
                marker.set()
                continue
            self._post(alert)

    def _post(self, alert: dict[str, Any]) -> None:
        try:
            body = json.dumps(self._format(alert)).encode("utf-8")
            request = urllib.request.Request(
                self._url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self._timeout):
                pass
        except Exception as exc:  # noqa: BLE001 — alerting must fail open
            logger.warning("alert delivery failed (%s); dropping", exc)

    def _format(self, alert: dict[str, Any]) -> dict[str, Any]:
        """Payload shape hook — subclasses adapt to a chat service."""
        return alert

    def __repr__(self) -> str:
        return f"{type(self).__name__}(target={self.describe()['target']!r})"


class SlackAlertExporter(WebhookAlertExporter):
    """WebhookAlertExporter shaped for Slack incoming webhooks."""

    def _format(self, alert: dict[str, Any]) -> dict[str, Any]:
        tool = f" tool={alert['tool_name']}" if alert.get("tool_name") else ""
        layers = ", ".join(alert.get("failing_layers", [])) or "none"
        return {
            "text": (
                f":rotating_light: agentegrity *{alert['action']}* — "
                f"adapter {alert['adapter']}, event {alert['event_type']}"
                f"{tool}, composite {alert['composite']}, "
                f"failing layers: {layers} "
                f"(session {alert['session_id']})"
            )
        }


__all__ = ["SlackAlertExporter", "WebhookAlertExporter"]
