"""
Base adapter protocol for framework integrations.

All framework adapters (Claude Agent SDK, LangGraph, OpenAI Agents SDK,
CrewAI) implement this Protocol so they can be used interchangeably
with the agentegrity evaluation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from agentegrity.core.evaluator import IntegrityScore
from agentegrity.core.profile import AgentProfile


class FrameworkAdapter(Protocol):
    """Protocol that all framework adapters must implement.

    An adapter instruments a specific agent framework by:
    1. Registering hooks/callbacks at framework extension points
    2. Collecting runtime context from those hooks
    3. Triggering integrity evaluations with the collected context
    4. Emitting structured FrameworkEvents for audit trails
    """

    @property
    def name(self) -> str:
        """Unique name for this adapter (e.g. 'claude', 'langgraph')."""
        ...

    @property
    def profile(self) -> AgentProfile:
        """The agent profile being monitored."""
        ...

    @property
    def events(self) -> list[FrameworkEvent]:
        """All events emitted by this adapter during the session."""
        ...

    def get_collected_context(self) -> dict[str, Any]:
        """Return the accumulated runtime context for evaluation."""
        ...

    async def on_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle a framework event and return hook output.

        Parameters
        ----------
        event_type : str
            The type of event (e.g. "pre_tool_use", "post_tool_use").
        event_data : dict
            Framework-specific event data.

        Returns
        -------
        dict
            Hook output that the framework will process (e.g. permission
            decisions, additional context). Return {} to allow without
            modification.
        """
        ...


@dataclass
class FrameworkEvent:
    """A structured event emitted by a framework adapter.

    Every adapter interaction produces a FrameworkEvent for the audit
    trail. Events include the raw framework data plus any evaluation
    result that was triggered.
    """

    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    adapter_name: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    evaluation_result: IntegrityScore | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "adapter_name": self.adapter_name,
            "data": self.data,
            "evaluation_result": (
                self.evaluation_result.to_dict()
                if self.evaluation_result
                else None
            ),
        }
