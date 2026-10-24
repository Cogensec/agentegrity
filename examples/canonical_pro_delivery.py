"""Offline demonstration: host observations -> canonical Pro sender.

This demonstrates the event shape and verified API response handling
using an in-memory transport. It does not contact Pro or any other network
service, and it does not imply that the tool performed an external effect.

Run: python examples/canonical_pro_delivery.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agentegrity.core.host_observer import HostAgentObserver, ToolCompletion
from agentegrity.exporters.canonical_pro import CanonicalProSender


def run_demo() -> list[str]:
    received_ids: list[str] = []

    def local_transport(
        endpoint: str, body: bytes, headers: Any, timeout: float
    ) -> tuple[int, bytes]:
        assert endpoint == "https://staging.example.test/canonical/events"
        assert headers["Authorization"] == "Bearer synthetic-demo-token"
        event = json.loads(body)
        assert set(event).isdisjoint({"prompt", "arguments", "credentials"})
        received_ids.append(event["event_id"])
        return 202, json.dumps({
            "ok": True,
            "event_id": event["event_id"],
            "source_assurance_effective": "unverified",
        }).encode("utf-8")

    observer = HostAgentObserver(
        agent_id="research-agent", run_id="offline-run",
        host="trusted-test-host", integration="python-host",
    )
    now = datetime.now(timezone.utc)
    observer.start(when=now)
    observer.tool_intent(
        action_id="read-1", tool_name="read_document", effect="read", when=now
    )
    observer.tool_outcome(
        action_id="read-1", completion=ToolCompletion.RETURNED, when=now
    )
    observer.finish(when=now)
    sender = CanonicalProSender(
        endpoint="https://staging.example.test/canonical/events",
        token="synthetic-demo-token", transport=local_transport,
    )
    receipts = [sender.send(event) for event in observer.events]
    assert len(received_ids) == 4
    assert all(r.source_assurance_effective == "unverified" for r in receipts)
    return received_ids


if __name__ == "__main__":
    ids = run_demo()
    print(f"Locally simulated Pro acknowledgements: {len(ids)}")
    print("Downstream agent identity, actions and effects remain unverified.")
