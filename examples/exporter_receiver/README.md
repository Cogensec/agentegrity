# Reference SessionExporter HTTP receiver

A small FastAPI app that implements the three-endpoint contract every
SessionExporter backend must satisfy. Lets you adopt the agentegrity
exporter wire format without depending on the commercial
`agentegrity-pro` dashboard.

## What it does

For each request, the app validates the JSON body against the
canonical schema in `schemas/exporter/`, logs a one-line summary to
stdout, and writes the full payload as a JSON-line so you can pipe it
into `jq` or `tee receiver.log`.

| Endpoint | SessionExporter hook | Schema |
|---|---|---|
| `POST /sessions` | `on_session_start` | `schemas/exporter/session_start.json` |
| `POST /sessions/{id}/events` | `on_event` (called N times) | `schemas/exporter/event.json` |
| `POST /sessions/{id}/end` | `on_session_end` | `schemas/exporter/session_end.json` |

Plus three convenience endpoints not in the contract:

- `GET /health` — liveness check.
- `GET /sessions` — list every session the receiver has seen.
- `GET /sessions/{id}` — full session detail (profile, events, summary).

## Run it

```bash
pip install -r examples/exporter_receiver/requirements.txt
uvicorn examples.exporter_receiver.app:app --host 0.0.0.0 --port 8787
```

Then point any agentegrity adapter at it:

```bash
AGENTEGRITY_URL=http://localhost:8787 python my_agent.py
```

`AGENTEGRITY_URL` is what the TypeScript `AgentegrityReporter` reads;
Python adapters use the same convention via the `[reporter]` config
shipped in v0.6.0+.

## What it is not

- **Not a production backend.** Sessions live in process memory and
  are dropped on restart. There's no auth, no rate limiting, no
  durable storage, no fan-out to multiple subscribers, no horizontal
  scaling story.
- **Not the commercial dashboard.** `agentegrity-pro` ships separately
  and adds the dashboard UI, multi-tenant auth, retention, analytics,
  and the rest of the product surface.
- **Not a SessionExporter implementation.** The receiver implements
  the *server* side of the exporter contract. The Python and TS
  clients are the *exporter* side; this app is what they talk to.

Use this code as the starting point for a real backend (swap the
in-memory dict for Postgres, add auth middleware, fan-out to your
SIEM, etc.) or as the integration-test target for your own adapter
wiring.

## Run the smoke tests

```bash
pip install -r examples/exporter_receiver/requirements.txt
pytest examples/exporter_receiver/test_app.py -v
```

The tests drive each endpoint via FastAPI's `TestClient` and assert
both the happy path (202 on a valid payload) and the validation path
(422 with detail on a malformed payload). They mirror the kind of
contract test a real implementer would write before turning their
backend on for real adapters.

## End-to-end with a Python adapter

This is what a one-shot manual integration looks like once the
receiver is running on `:8787`:

```python
import asyncio
from agentegrity.claude import hooks, register_exporter, report
from agentegrity.adapters.base import HTTPSessionExporter  # ships in v0.6.0+

register_exporter(HTTPSessionExporter(base_url="http://localhost:8787"))

# ... run your Claude SDK agent with hooks=hooks() ...

print(asyncio.run(report()))
# In the receiver's terminal you'll see one /sessions, N /events,
# and one /sessions/{id}/end go past as JSON-lines.
```

The TypeScript `AgentegrityReporter` is wired the same way — set
`AGENTEGRITY_URL=http://localhost:8787` and the built-in HTTP
reporter inside every `@agentegrity/<framework>` package will hit
this receiver.
