"""Host-owned, bounded, metadata-only agent lifecycle event recording.

This is an opt-in observation facility for any agent host, not a tool
enforcement or identity-attestation layer. The trusted application host
records actual lifecycle transitions; prompts and tool arguments are never
accepted or captured. Recorded returns are not downstream effect verification.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from agentegrity.core.agent_events import (
    CanonicalAgentEvent,
    EventSource,
    SourceAssurance,
)


class ToolCompletion(str, Enum):
    RETURNED = "returned_unverified"
    RAISED = "raised_uncertain"
    CANCELLED = "cancelled_uncertain"
    ABANDONED = "abandoned_uncertain"


class EventCapacityExceeded(RuntimeError):
    """Event capture refused; do not imply uninterrupted observability."""


class HostAgentObserver:
    """Record typed lifecycle/tool metadata without storing agent payloads.

    Host-callable methods are protected by a lock so tool completions and
    run shutdown cannot race into contradictory traces. The record budget
    reserves space for pending tool completions and the final run event.
    Capture failures are *raised*, never silently dropped.

    This observer does not enforce tool calls. Integrate ActionSecurityGate
    independently at a trusted executor for actual blocking.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        run_id: str,
        host: str,
        integration: str,
        max_events: int = 1000,
    ) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 2:
            raise ValueError("max_events must be an integer of at least 2")
        self._agent_id = agent_id
        self._run_id = run_id
        # A claimed host label is not independently verified.
        self._source = EventSource(
            host=host, integration=integration, assurance=SourceAssurance.HOST_ASSERTED
        )
        self._max_events = max_events
        self._lock = threading.RLock()
        self._events: list[CanonicalAgentEvent] = []
        self._pending: dict[str, tuple[str, str | None]] = {}
        self._used_ids: set[str] = set()
        self._started = False
        self._closed = False
        # Validate identity up front without inventing a start event.
        CanonicalAgentEvent(
            event_id=uuid4().hex,
            event_type="agent.initialized",
            occurred_at=datetime.now(timezone.utc),
            agent_id=self._agent_id,
            run_id=self._run_id,
            source=self._source,
        )

    def _emit(
        self,
        event_type: str,
        *,
        when: datetime | None = None,
        action_id: str | None = None,
        tool_name: str | None = None,
        effect: str | None = None,
        outcome: str | None = None,
    ) -> CanonicalAgentEvent:
        event = CanonicalAgentEvent(
            event_id=uuid4().hex,
            event_type=event_type,
            occurred_at=when if when is not None else datetime.now(timezone.utc),
            agent_id=self._agent_id,
            run_id=self._run_id,
            source=self._source,
            action_id=action_id,
            tool_name=tool_name,
            effect=effect,
            outcome=outcome,
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> tuple[CanonicalAgentEvent, ...]:
        """Immutable snapshot of recorded events, not authenticated evidence."""
        with self._lock:
            return tuple(self._events)

    @property
    def active_actions(self) -> tuple[str, ...]:
        """Opaque action IDs still awaiting a host-observed tool outcome."""
        with self._lock:
            return tuple(self._pending)

    def start(self, *, when: datetime | None = None) -> CanonicalAgentEvent:
        """Host signals that this execution run actually started."""
        with self._lock:
            if self._started:
                raise ValueError("run already started")
            event = self._emit("agent.started", when=when)
            self._started = True
            return event

    def tool_intent(
        self,
        *,
        action_id: str,
        tool_name: str,
        effect: str | None = None,
        when: datetime | None = None,
    ) -> CanonicalAgentEvent:
        """Host records a tool request; does *not* authorize it."""
        with self._lock:
            if not self._started or self._closed:
                raise ValueError("run is not active")
            if action_id in self._used_ids:
                raise ValueError("action_id already seen in this run")
            # One new intent + one reserved outcome + one final run event,
            # plus existing pending completions. Never evict evidence.
            if len(self._events) + len(self._pending) + 3 > self._max_events:
                raise EventCapacityExceeded("event recording limit reached")
            event = self._emit(
                "tool.intent", when=when, action_id=action_id,
                tool_name=tool_name, effect=effect,
            )
            self._used_ids.add(action_id)
            self._pending[action_id] = (tool_name, effect)
            return event

    def tool_outcome(
        self,
        *,
        action_id: str,
        completion: ToolCompletion,
        when: datetime | None = None,
    ) -> CanonicalAgentEvent:
        """Host reports a callable outcome, not its external side effects."""
        with self._lock:
            if self._closed or action_id not in self._pending:
                raise ValueError("unknown or completed action_id")
            if not isinstance(completion, ToolCompletion):
                raise TypeError("completion must be a ToolCompletion")
            tool_name, effect = self._pending[action_id]
            event = self._emit(
                "tool.result" if completion == ToolCompletion.RETURNED else "tool.failed",
                action_id=action_id, tool_name=tool_name, effect=effect,
                outcome=completion.value, when=when,
            )
            del self._pending[action_id]
            return event

    def finish(self, *, when: datetime | None = None) -> CanonicalAgentEvent:
        """Close recording, marking unfinished tools uncertain, never failed.

        Each tool intent has reserved room for its outcome. This records
        abandonment, not a rollback or independent effect verification.
        """
        with self._lock:
            if not self._started or self._closed:
                raise ValueError("run is not active")
            for action_id in tuple(self._pending):
                self.tool_outcome(
                    action_id=action_id, completion=ToolCompletion.ABANDONED, when=when
                )
            event = self._emit("agent.ended", when=when)
            self._closed = True
            return event

    def export(self) -> list[dict[str, object]]:
        """JSON-ready local snapshot; never auto-uploads or includes payloads."""
        with self._lock:
            return [event.to_dict() for event in self._events]
