# Canonical Pro event delivery (opt-in Python sender)

**Status:** Offline-tested interoperability sender, not a live deployed
integration. This PR builds on [OSS #113](https://github.com/Cogensec/agentegrity/pull/113)
(which depends on [OSS #110](https://github.com/Cogensec/agentegrity/pull/110)).
**Merge the prerequisite contract and observer PRs first.** Pro must have
[Pro #113](https://github.com/requie/agentegrity-pro/pull/113) and
[Pro #114](https://github.com/requie/agentegrity-pro/pull/114) deployed
before live delivery is meaningful.

## What works

A trusted host can use `HostAgentObserver` to create canonical metadata
events and explicitly submit individual events to the authenticated
`POST /canonical/events` endpoint.

```python
from agentegrity import HostAgentObserver, CanonicalProSender, ToolCompletion

observer = HostAgentObserver(
    agent_id="research-agent", run_id="run-1",
    host="my-trusted-host", integration="python-host",
)
observer.start()
observer.tool_intent(action_id="read-1", tool_name="read_document", effect="read")
observer.tool_outcome(action_id="read-1", completion=ToolCompletion.RETURNED)
observer.finish()

sender = CanonicalProSender(
    endpoint="https://YOUR-APP-HOST/canonical/events",
    token=trusted_workspace_ingest_token,
)
# Deliberate opt-in: send only with approved workspace credentials and endpoint.
for event in observer.events:
    receipt = sender.send(event)
    assert receipt.source_assurance_effective == "unverified"
```

**Do not** put workspace tokens in prompts, source code, agent tool arguments,
application logs, query strings or event content. Read them from an approved
host secret manager. Do not run against production without approval.

## Exact security/transport behaviors

- HTTPS only, exact `/canonical/events` path. Redirects are not followed;
  bearer credentials cannot be sent to redirect destinations.
- Uses Python's standard library; no new dependency.
- No network activity on module import, sender construction, or observation.
  `sender.send(event)` makes exactly one POST (no auto-retry or export).
- Accepts **only** canonical `CanonicalAgentEvent` objects, revalidates
  the wire grammar, and blocks events over Pro's 8 KiB admission limit.
- Token is supplied by the trusted host, not embedded in event metadata.
- A 202 acknowledges only when JSON has `ok=true`, exactly the requested
  event ID and `source_assurance_effective=unverified`.
- A 409 is a duplicate event ID; it is *not treated as successful delivery*.
  A 408/425/429, 5xx, network error, or malformed 202 acknowledgement
  means the outcome is uncertain. No automatic retries occur. Operators
  must reconcile a possible previous commit before explicitly replaying.
- 401/403/422 are rejection conditions; the exception never quotes
  server-controlled response content.
- No raw prompts, retrieved pages, tool arguments or other arbitrary
  payloads are passed in the request.

## Test without sending data to Pro

```bash
python -m pytest tests/test_canonical_pro_sender.py -v
python examples/canonical_pro_delivery.py
```

The demonstration uses a local injected fake transport. It validates
protocol shape and acknowledgement handling but is **not** a real Pro
PostgreSQL/network test. The existing Pro test suite separately verifies
tenant-bound ingestion, replay and strict payload admission.

## Assumptions made and requiring approval for real staging

1. The first end-to-end host is **custom Python** with explicit
   lifecycle hooks, not universal interception of arbitrary framework runs.
2. Opt-in delivery uses a **workspace-level** Pro ingestion token stored
   in a host secret manager. It authenticates the workspace only, not an
   individual agent, signed event or external effect.
3. Events may arrive no more than 10 minutes after `occurred_at` because
   Pro's current ingestion endpoint refuses older events. There is no
   durable retry queue or offline backfill; deployment architecture must
   tolerate that gap and surface delivery failures.
4. The sender emits canonical **metadata only**. It does not score actions,
   validate postconditions, attest to identities, rollback effects, or
   replace `TrustedActionGateway` enforcement.
5. This SDK ships as OSS, while the default transport points only to an
   explicitly configured Pro-compatible HTTPS endpoint, not to a
   Cogensec-owned server by default.

Before testing an actual environment, obtain explicit authorization for
the staging endpoint, scoped credentials, allowed synthetic test cases,
and no production data transfer. No credentials or live requests were used
to develop this PR.
