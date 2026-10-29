"""Synthetic bank-agent authority -> canonical evidence -> Pro delivery demo.

This is intentionally OFFLINE. The host installs its own execution gateway and
mandate; an untrusted agent never creates authority. The "Pro" transport is a
memory-only fake with no credentials, actual network, accounts or bank system.
It demonstrates *blocked execution*, canonical observation and receipt-shape
compatibility, not real transactions, authenticated source identity or recovery.

Run: python examples/banking_gateway_canonical_trace.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from agentegrity.core.action_security import (
    AuthorizationDenied,
    EffectStatus,
    EffectType,
    TaskMandate,
    ToolAuthority,
)
from agentegrity.core.agent_events import CanonicalAgentEvent
from agentegrity.core.host_observer import HostAgentObserver, ToolCompletion
from agentegrity.exporters.canonical_pro import CanonicalProSender
from agentegrity.sdk.gateway import GatewayTool, TrustedActionGateway


def run_demo() -> dict[str, Any]:
    """Exercise real SDK boundaries with synthetic inputs and no external I/O."""
    now = datetime.now(timezone.utc)
    executed: list[str] = []
    posted: list[dict[str, Any]] = []

    def read_balance(account_id: str) -> str:
        executed.append("read_balance")
        assert account_id == "synthetic-account-1"
        return "synthetic-balance"

    def transfer_money(account_id: str) -> str:
        # This executor MUST NEVER run under the read-only task mandate.
        executed.append("transfer_money")
        return "synthetic-transfer-requested"

    mandate = TaskMandate(
        mandate_id="synthetic-read-only-mandate",
        principal_id="trusted-synthetic-operator",
        agent_id="synthetic-bank-agent",
        run_id="synthetic-bank-run",
        expires_at=now + timedelta(minutes=10),
        allowed_tools=frozenset({"read_balance", "transfer_money"}),
        allowed_resources=frozenset({"synthetic-account-1"}),
        allowed_effects=frozenset({EffectType.READ}),
    )
    gateway = TrustedActionGateway(
        mandate=mandate,
        tools={
            "read_balance": GatewayTool(
                ToolAuthority(EffectType.READ, resource_argument="account_id"),
                read_balance,
            ),
            "transfer_money": GatewayTool(
                ToolAuthority(EffectType.FINANCIAL_WRITE, resource_argument="account_id"),
                transfer_money,
            ),
        },
    )
    observer = HostAgentObserver(
        agent_id=mandate.agent_id,
        run_id=mandate.run_id or "synthetic-bank-run",
        host="trusted-synthetic-bank-host",
        integration="python-trusted-gateway",
    )
    observer.start(when=now)

    # The observer is called by the host; gateway.invoke is the actual veto point.
    observer.tool_intent(
        action_id="read-1", tool_name="read_balance", effect="read", when=now
    )
    permitted = gateway.invoke(
        "read_balance", {"account_id": "synthetic-account-1"},
        action_id="read-1", now=now,
    )
    assert permitted.result == "synthetic-balance"
    assert permitted.receipt.status is EffectStatus.PENDING
    observer.tool_outcome(
        action_id="read-1", completion=ToolCompletion.RETURNED, when=now,
    )

    # No observer tool.result is emitted for the denied financial action,
    # since its actual executor was never called. Record a policy.verdict claim
    # separately, without fabricating a tool outcome or external effect.
    try:
        gateway.invoke(
            "transfer_money", {"account_id": "synthetic-account-1"},
            action_id="transfer-1", now=now,
        )
    except AuthorizationDenied as exc:
        denial_reason = exc.verdict.reason
    else:
        raise AssertionError("financial write must be denied at execution")

    assert executed == ["read_balance"]
    verdict_event = CanonicalAgentEvent(
        event_id=uuid4().hex,
        event_type="policy.verdict",
        occurred_at=now,
        agent_id=mandate.agent_id,
        run_id=mandate.run_id or "synthetic-bank-run",
        source=observer.events[0].source,
        action_id="transfer-1",
        tool_name="transfer_money",
        effect="financial_write",
        outcome="denied",
    )
    observer.finish(when=now)
    events = [*observer.events[:-1], verdict_event, observer.events[-1]]

    def fake_pro(
        endpoint: str, body: bytes, headers: Any, timeout: float
    ) -> tuple[int, bytes]:
        assert endpoint == "https://synthetic.example.test/canonical/events"
        assert headers["Authorization"] == "Bearer offline-synthetic-token"
        assert timeout > 0
        wire = json.loads(body)
        assert "arguments" not in wire and "prompt" not in wire
        posted.append(wire)
        # Pro's receipt means only that a workspace token was accepted.
        return 202, json.dumps({
            "ok": True,
            "event_id": wire["event_id"],
            "source_assurance_effective": "unverified",
        }).encode("utf-8")

    sender = CanonicalProSender(
        endpoint="https://synthetic.example.test/canonical/events",
        token="offline-synthetic-token",
        transport=fake_pro,
    )
    receipts = [sender.send(event) for event in events]
    assert all(receipt.source_assurance_effective == "unverified"
               for receipt in receipts)
    assert not gateway.gate.effects_verified(["read-1"])

    return {
        "executed_tools": executed,
        "denial_reason": denial_reason,
        "read_effect_status": permitted.receipt.status.value,
        "posted_events": posted,
        "acknowledged_count": len(receipts),
        "independently_verified": False,
    }


if __name__ == "__main__":
    result = run_demo()
    print("Synthetic tools executed:", result["executed_tools"])
    print("Financial write was denied:", result["denial_reason"])
    print("Canonical events acknowledged by local fake:", result["acknowledged_count"])
    print("Downstream effects independently verified: no")
