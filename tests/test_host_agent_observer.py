"""Host-side universal event conformance for non-coding and coding agents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from agentegrity.core.host_observer import (
    EventCapacityExceeded,
    HostAgentObserver,
    ToolCompletion,
)

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


def _observer(max_events: int = 20) -> HostAgentObserver:
    return HostAgentObserver(
        agent_id="customer-agent",
        run_id="run-01",
        host="customer-owned-orchestrator",
        integration="host-sdk",
        max_events=max_events,
    )


@pytest.mark.parametrize(("agent_category", "tool_name", "effect"), [
    ("financial", "prepare_transfer", "financial_write"),
    ("enterprise", "update_crm", "write"),
    ("customer_support", "issue_refund", "financial_write"),
    ("browser", "navigate_page", "read"),
    ("research", "search_index", "read"),
    ("infrastructure", "set_iam_role", "write"),
    ("coding", "write_file", "write"),
    ("multi_agent", "send_to_worker", "external_send"),
    ("physical_simulator", "simulated_arm_motion", "unknown"),
])
def test_host_can_record_category_without_capturing_inputs(
    agent_category: str, tool_name: str, effect: str,
) -> None:
    observer = _observer()
    observer.start(when=NOW)
    intent = observer.tool_intent(
        action_id="action-1", tool_name=tool_name, effect=effect, when=NOW
    )
    assert intent.to_dict()["event_type"] == "tool.intent"
    outcome = observer.tool_outcome(
        action_id="action-1", completion=ToolCompletion.RETURNED, when=NOW
    )
    observer.finish(when=NOW)
    assert outcome.to_dict()["outcome"] == "returned_unverified"
    assert len(observer.export()) == 4
    assert all(
        "data" not in item and "arguments" not in item
        and "prompt" not in item and "evaluation_result" not in item
        for item in observer.export()
    )
    assert agent_category  # distinguishes independent host adapters, not support certification


def test_lifecycle_is_explicit_and_ordered() -> None:
    observer = _observer()
    assert observer.events == ()
    with pytest.raises(ValueError, match="not active"):
        observer.tool_intent(action_id="a", tool_name="read")
    with pytest.raises(ValueError, match="not active"):
        observer.finish()
    started = observer.start(when=NOW)
    assert started.event_type == "agent.started"
    with pytest.raises(ValueError, match="already started"):
        observer.start()
    ended = observer.finish(when=NOW)
    assert ended.event_type == "agent.ended"
    with pytest.raises(ValueError, match="not active"):
        observer.finish()


def test_running_out_of_capacity_does_not_drop_unfinished_actions() -> None:
    observer = _observer(max_events=4)
    observer.start(when=NOW)
    observer.tool_intent(action_id="one", tool_name="search", effect="read")
    with pytest.raises(EventCapacityExceeded, match="recording limit"):
        observer.tool_intent(action_id="two", tool_name="refund", effect="financial_write")
    assert observer.active_actions == ("one",)
    observer.finish(when=NOW)
    assert [event.event_type for event in observer.events] == [
        "agent.started", "tool.intent", "tool.failed", "agent.ended"
    ]
    assert observer.events[2].outcome == "abandoned_uncertain"
    assert observer.active_actions == ()


def test_ended_run_does_not_mark_pending_effect_as_failed_or_verified() -> None:
    observer = _observer()
    observer.start()
    observer.tool_intent(action_id="pending-charge", tool_name="charge", effect="financial_write")
    observer.finish()
    records = observer.export()
    assert records[-2]["outcome"] == "abandoned_uncertain"
    assert all("independently_verified" not in record for record in records)
    assert all("effect_receipt" not in record for record in records)


def test_duplicate_action_ids_are_rejected_even_after_completion() -> None:
    observer = _observer()
    observer.start()
    observer.tool_intent(action_id="a", tool_name="search")
    observer.tool_outcome(action_id="a", completion=ToolCompletion.RETURNED)
    with pytest.raises(ValueError, match="already seen"):
        observer.tool_intent(action_id="a", tool_name="write")
    assert len(observer.events) == 3


@pytest.mark.parametrize("completion", [ToolCompletion.RAISED, ToolCompletion.CANCELLED])
def test_unsuccessful_tool_return_is_not_a_verified_external_failure(
    completion: ToolCompletion,
) -> None:
    observer = _observer()
    observer.start()
    observer.tool_intent(action_id="uncertain", tool_name="transfer", effect="financial_write")
    event = observer.tool_outcome(action_id="uncertain", completion=completion)
    assert event.event_type == "tool.failed"
    assert event.outcome.endswith("_uncertain")


def test_bad_time_does_not_change_action_state() -> None:
    observer = _observer()
    observer.start()
    with pytest.raises(ValueError, match="timezone-aware"):
        observer.tool_intent(
            action_id="a", tool_name="do_work",
            when=datetime(2026, 10, 22),
        )
    assert observer.active_actions == ()
    assert len(observer.events) == 1


def test_unknown_completion_does_not_consume_action() -> None:
    observer = _observer()
    observer.start()
    observer.tool_intent(action_id="a", tool_name="read")
    with pytest.raises(TypeError, match="ToolCompletion"):
        observer.tool_outcome(
            action_id="a", completion="returned_unverified"  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="unknown or completed"):
        observer.tool_outcome(action_id="not-pending", completion=ToolCompletion.RETURNED)
    assert observer.active_actions == ("a",)


@pytest.mark.parametrize("limit", [True, False, -2, 0, 1, 2.4, "100"])
def test_invalid_capacity_rejected(limit: object) -> None:
    with pytest.raises(ValueError, match="max_events"):
        _observer(max_events=limit)  # type: ignore[arg-type]


def test_thread_safe_actions_have_distinct_events_and_complete_cleanly() -> None:
    observer = _observer(max_events=300)
    observer.start()

    def process(index: int) -> None:
        action_id = f"op-{index}"
        observer.tool_intent(action_id=action_id, tool_name="safe_read")
        observer.tool_outcome(action_id=action_id, completion=ToolCompletion.RETURNED)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(process, range(100)))

    observer.finish()
    assert len(observer.events) == 202
    assert len({event.event_id for event in observer.events}) == 202
    assert len([e for e in observer.events if e.event_type == "tool.intent"]) == 100
    assert observer.active_actions == ()
