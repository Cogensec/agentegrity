"""Reference SessionExporter HTTP receiver.

A tiny FastAPI app that implements the three-endpoint contract every
SessionExporter backend must satisfy (per ``schemas/openapi.yaml``)::

    POST /sessions                       → on_session_start
    POST /sessions/{session_id}/events   → on_event (called N times)
    POST /sessions/{session_id}/end      → on_session_end

Each request body is validated against the corresponding JSON Schema in
``schemas/exporter/``. Validated payloads are logged to stdout as
JSON-lines so you can pipe the output into ``jq`` or ``tee bench.log``.

This exists so users can adopt the agentegrity exporter wire format
without depending on the commercial ``agentegrity-pro`` dashboard. It
is **not** a production backend — sessions live in process memory and
are dropped on restart, there's no auth, no rate limiting, no
durable storage. Use it as the integration test target for your own
adapter wiring or as the starting point for a real backend.

Run it::

    pip install -r examples/exporter_receiver/requirements.txt
    uvicorn examples.exporter_receiver.app:app --host 0.0.0.0 --port 8787

Then point any agentegrity adapter at it::

    AGENTEGRITY_URL=http://localhost:8787 python my_agent.py
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

logger = logging.getLogger("agentegrity.exporter_receiver")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)


# ---------------------------------------------------------------------------
# Schema loading + validators
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_DIR = _REPO_ROOT / "schemas" / "exporter"


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _build_validator(schema_name: str) -> Draft202012Validator:
    """Build a Draft 2020-12 validator that can resolve sibling
    ``$ref`` references like ``common.json#/$defs/AgentProfile`` against
    the on-disk schemas in ``schemas/exporter/``.
    """
    schema = _load_schema(schema_name)
    common = _load_schema("common.json")
    # The schemas reference each other via relative URLs (``common.json``
    # without scheme). Register both under their relative URI so the
    # referencing library resolves them locally rather than trying to
    # fetch over the network.
    registry: Registry[Any] = Registry().with_resources(
        [
            ("common.json", Resource.from_contents(common)),
            (schema_name, Resource.from_contents(schema)),
            # Also register the canonical $id URLs in case a future
            # schema bump promotes them.
            (
                schema.get("$id", ""),
                Resource.from_contents(schema),
            ),
            (
                common.get("$id", ""),
                Resource.from_contents(common),
            ),
        ]
    )
    return Draft202012Validator(schema, registry=registry)


_session_start_validator = _build_validator("session_start.json")
_event_validator = _build_validator("event.json")
_session_end_validator = _build_validator("session_end.json")


# ---------------------------------------------------------------------------
# In-memory session store. Not durable. Don't use this for production.
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.session_id: str = payload["session_id"]
        self.adapter_name: str = payload["adapter_name"]
        self.profile: dict[str, Any] = payload["profile"]
        self.events: list[dict[str, Any]] = []
        self.summary: dict[str, Any] | None = None


_sessions: dict[str, _Session] = {}
_event_counts: defaultdict[str, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Agentegrity reference exporter receiver",
    version="0.6.0",
    description=(
        "Reference implementation of the SessionExporter HTTP API. "
        "Validates every payload against schemas/exporter/*.json. "
        "Sessions are in-memory only — restart wipes everything."
    ),
)


def _validate(validator: Draft202012Validator, payload: dict[str, Any]) -> None:
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
    if errors:
        # 422 mirrors the OpenAPI spec for "well-formed JSON, schema
        # validation failed". Limit detail to the first 5 errors so a
        # noisy client doesn't fill the response.
        details = [
            {
                "path": list(err.path),
                "message": err.message,
                "schema_path": list(err.schema_path),
            }
            for err in errors[:5]
        ]
        raise HTTPException(
            status_code=422,
            detail={"errors": details, "total": len(errors)},
        )


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 — malformed JSON is a client problem
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected JSON object")
    return body


@app.post("/sessions", status_code=202)
async def start_session(request: Request) -> JSONResponse:
    payload = await _read_json(request)
    _validate(_session_start_validator, payload)
    session = _Session(payload)
    _sessions[session.session_id] = session
    logger.info(
        "session_start: id=%s adapter=%s agent_id=%s",
        session.session_id,
        session.adapter_name,
        session.profile.get("agent_id", "?"),
    )
    print(json.dumps({"event": "session_start", "payload": payload}))
    return JSONResponse({"accepted": True}, status_code=202)


@app.post("/sessions/{session_id}/events", status_code=202)
async def append_event(session_id: str, request: Request) -> JSONResponse:
    payload = await _read_json(request)
    _validate(_event_validator, payload)
    if payload.get("session_id") != session_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"path session_id={session_id!r} does not match body "
                f"session_id={payload.get('session_id')!r}"
            ),
        )
    session = _sessions.get(session_id)
    if session is None:
        # Tolerate out-of-order events — accept them but tag the session
        # as orphaned so the operator can spot it. Mirrors what a real
        # backend would do for retried events after a restart.
        logger.warning("event for unknown session %s — accepting as orphan", session_id)
        _sessions[session_id] = session = _Session(
            {
                "session_id": session_id,
                "adapter_name": payload.get("event", {}).get("adapter_name", "orphan"),
                "profile": {},
            }
        )
    session.events.append(payload["event"])
    _event_counts[session_id] += 1
    logger.info(
        "event: session=%s type=%s n=%d",
        session_id,
        payload["event"].get("event_type"),
        _event_counts[session_id],
    )
    print(json.dumps({"event": "on_event", "payload": payload}))
    return JSONResponse({"accepted": True}, status_code=202)


@app.post("/sessions/{session_id}/end", status_code=202)
async def end_session(session_id: str, request: Request) -> JSONResponse:
    payload = await _read_json(request)
    _validate(_session_end_validator, payload)
    if payload.get("session_id") != session_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"path session_id={session_id!r} does not match body "
                f"session_id={payload.get('session_id')!r}"
            ),
        )
    session = _sessions.get(session_id)
    if session is None:
        logger.warning("session_end for unknown session %s — ignoring", session_id)
    else:
        session.summary = payload["summary"]
        logger.info(
            "session_end: id=%s events=%d evals=%d chain_hash_linked=%s",
            session_id,
            session.summary.get("events", 0),
            session.summary.get("evaluations", 0),
            session.summary.get("chain_hash_linked", "?"),
        )
    print(json.dumps({"event": "session_end", "payload": payload}))
    return JSONResponse({"accepted": True}, status_code=202)


@app.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    """Convenience endpoint — not part of the SessionExporter contract.

    Useful when poking around with curl or a browser to confirm that a
    given adapter actually fanned events out to this receiver.
    """
    return {
        "sessions": [
            {
                "session_id": sid,
                "adapter_name": s.adapter_name,
                "events": len(s.events),
                "ended": s.summary is not None,
                "agent_id": s.profile.get("agent_id"),
            }
            for sid, s in _sessions.items()
        ],
        "total": len(_sessions),
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    s = _sessions.get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": s.session_id,
        "adapter_name": s.adapter_name,
        "profile": s.profile,
        "events": s.events,
        "summary": s.summary,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "sessions": len(_sessions)}
