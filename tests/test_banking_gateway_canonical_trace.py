"""Cross-boundary synthetic banking demo: no network, no financial effect."""

from __future__ import annotations

from agentegrity.core.agent_events import CanonicalAgentEvent
from examples.banking_gateway_canonical_trace import run_demo


def test_denied_financial_action_never_reaches_executor() -> None:
    result = run_demo()
    assert result["executed_tools"] == ["read_balance"]
    assert result["denial_reason"]
    assert result["read_effect_status"] == "pending"
    assert result["independently_verified"] is False


def test_observer_and_authority_verdict_deliver_safe_canonical_metadata() -> None:
    result = run_demo()
    events = result["posted_events"]
    assert result["acknowledged_count"] == len(events) == 5
    assert [event["event_type"] for event in events] == [
        "agent.started",
        "tool.intent",
        "tool.result",
        "policy.verdict",
        "agent.ended",
    ]
    assert events[1]["action_id"] == "read-1"
    assert events[2]["outcome"] == "returned_unverified"
    assert events[3]["action_id"] == "transfer-1"
    assert events[3]["tool_name"] == "transfer_money"
    assert events[3]["outcome"] == "denied"
    # An authorization rejection is not a returned downstream tool effect.
    assert not any(
        event["event_type"] == "tool.result"
        and event.get("action_id") == "transfer-1"
        for event in events
    )
    for event in events:
        assert CanonicalAgentEvent.from_dict(event).to_dict() == event
        assert event["source"]["assurance"] == "host_asserted"
        assert event["agent_id"] == "synthetic-bank-agent"
        assert event["run_id"] == "synthetic-bank-run"
        assert not ({"prompt", "arguments", "credentials", "account_id", "balance"} & set(event))
    assert "synthetic-account-1" not in str(events)
    assert "offline-synthetic-token" not in str(events)
