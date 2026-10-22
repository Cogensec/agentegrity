"""Explicit, metadata-only canonical event delivery to an Agentegrity Pro API.

No network call occurs during construction, import, or host observation.
Sending is an opt-in method call with an explicit HTTPS endpoint and
workspace ingestion token. There is no background queue, automatic retry,
fallback telemetry, or implied evidence/source verification.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agentegrity.core.agent_events import CanonicalAgentEvent

_MAX_EVENT_BYTES = 8192
_MAX_RESPONSE_BYTES = 4096
Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]


class CanonicalDeliveryError(RuntimeError):
    """Delivery is not a verified downstream action or agent attestation."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CanonicalDeliveryRejected(CanonicalDeliveryError):
    """Server explicitly refused an event (e.g., unauthorized or invalid)."""


class CanonicalDeliveryDuplicate(CanonicalDeliveryError):
    """A 409 is not an authenticated success receipt; inspect server state."""


class CanonicalDeliveryUncertain(CanonicalDeliveryError):
    """Network failure or ambiguous response: server acceptance is unknown."""


@dataclass(frozen=True)
class CanonicalDeliveryReceipt:
    """Only confirms endpoint acknowledgement, never integrity or effect proof."""

    event_id: str
    source_assurance_effective: str


class _NoRedirect(HTTPRedirectHandler):
    """Never forward the workspace bearer token across HTTP redirects."""

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _https_post(
    url: str,
    content: bytes,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    opener = build_opener(_NoRedirect())
    req = Request(url, data=content, headers=dict(headers), method="POST")
    try:
        with opener.open(req, timeout=timeout) as response:
            status = int(response.status)
            return status, response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        # Do not expose server-controlled response bodies or redirect Locations.
        return int(exc.code), b""


class CanonicalProSender:
    """User-invoked TLS sender for metadata-only CanonicalAgentEvent objects.

    The trusted application provides an endpoint and workspace-level token.
    These *do not* authenticate the host, individual agent, downstream effect,
    or a claimed evidence reference. A caller must not interpret delivery
    as a successful authorization, settled payment, or completed recovery.

    A caller-controlled transport can replace HTTPS for isolated tests.
    Transport failures and 429/5xx statuses are ambiguous, so this class
    does not silently retry: Pro deduplicates by workspace/event ID, and
    a replay might legitimately return 409.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        token: str,
        timeout: float = 5.0,
        transport: Transport | None = None,
    ) -> None:
        if not isinstance(endpoint, str):
            raise ValueError("endpoint must be an HTTPS canonical ingestion URL")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/canonical/events"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint must be an HTTPS /canonical/events URL")
        if (
            not isinstance(token, str) or not token.strip()
            or any(ch in token for ch in "\r\n\x00")
        ):
            raise ValueError("a nonempty workspace ingestion token is required")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (float, int))
            or not 0 < timeout <= 30
        ):
            raise ValueError("timeout must be between 0 and 30 seconds")
        if transport is not None and not callable(transport):
            raise TypeError("transport must be callable")
        self._endpoint = endpoint
        self._token = token
        self._timeout = float(timeout)
        self._transport = transport if transport is not None else _https_post

    def send(self, event: CanonicalAgentEvent) -> CanonicalDeliveryReceipt:
        """Explicitly POST one canonical event; never collect arbitrary payloads."""
        if not isinstance(event, CanonicalAgentEvent):
            raise TypeError("send accepts only CanonicalAgentEvent")
        wire = event.to_dict()
        # Use the SDK's strict wire parser to prevent emitting an event that
        # the canonical contract itself must reject.
        CanonicalAgentEvent.from_dict(wire)
        encoded = json.dumps(
            wire, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > _MAX_EVENT_BYTES:
            raise ValueError("canonical event exceeds Pro 8 KiB boundary")
        headers = {
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            status, body = self._transport(
                self._endpoint, encoded, headers, self._timeout
            )
        except Exception:
            # Even a timeout may happen *after* a server commit.
            # Do not leak endpoint/token or assume it was not accepted.
            raise CanonicalDeliveryUncertain(
                "canonical event acceptance is unknown"
            ) from None
        if status == 202:
            if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
                raise CanonicalDeliveryUncertain(
                    "canonical endpoint acknowledgement is not verifiable", status
                )
            try:
                response: Any = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                raise CanonicalDeliveryUncertain(
                    "canonical endpoint acknowledgement is not verifiable", status
                ) from None
            if not (
                isinstance(response, dict)
                and response.get("ok") is True
                and response.get("event_id") == wire["event_id"]
                and response.get("source_assurance_effective") == "unverified"
            ):
                raise CanonicalDeliveryUncertain(
                    "canonical endpoint acknowledgement is not verifiable", status
                )
            return CanonicalDeliveryReceipt(
                event_id=event.event_id,
                source_assurance_effective="unverified",
            )
        if status == 409:
            raise CanonicalDeliveryDuplicate(
                "canonical event ID already exists; acceptance not established", status
            )
        if status in (408, 425, 429) or 500 <= status <= 599:
            raise CanonicalDeliveryUncertain(
                "canonical event acceptance is unknown", status
            )
        raise CanonicalDeliveryRejected(
            "canonical event rejected by endpoint", status
        )
