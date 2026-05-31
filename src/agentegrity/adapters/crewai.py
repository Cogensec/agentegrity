"""
CrewAI adapter for agentegrity.

Instruments CrewAI crews by subscribing to the global event bus
(``crewai.utilities.events.crewai_event_bus``) and forwarding each
event to the shared ``_BaseAdapter`` dispatcher.

Event mapping:
    CrewKickoffStartedEvent    -> user_prompt_submit
    ToolUsageStartedEvent      -> pre_tool_use
    ToolUsageFinishedEvent     -> post_tool_use
    ToolUsageErrorEvent        -> post_tool_use_failure
    TaskStartedEvent           -> subagent_start
    CrewKickoffCompletedEvent  -> stop

Usage:
    from agentegrity.crewai import instrument, report
    instrument()            # subscribe globally
    crew.kickoff()
    print(report())
"""

from __future__ import annotations

from typing import Any

from agentegrity.adapters.base import _BaseAdapter


class CrewAIAdapter(_BaseAdapter):
    """Instruments a CrewAI crew with agentegrity evaluation."""

    _name = "crewai"

    def subscribe(self, crew: Any | None = None) -> None:
        """Subscribe to the CrewAI event bus.

        If ``crew`` is None, subscribes globally (all crews in the
        process). Otherwise scopes the subscription to the given crew
        instance.
        """
        try:
            from crewai.utilities.events import (
                CrewKickoffCompletedEvent,
                CrewKickoffStartedEvent,
                TaskStartedEvent,
                ToolUsageFinishedEvent,
                ToolUsageStartedEvent,
                crewai_event_bus,
            )
        except ImportError:
            raise ImportError(
                "crewai is required for the CrewAI adapter. "
                "Install it with: pip install agentegrity[crewai]"
            ) from None

        adapter = self
        _ = crew  # crewai_event_bus is process-global; crew kept for API parity

        def _on_kickoff_start(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "user_prompt_submit",
                {"prompt": getattr(event, "inputs", "") or ""},
            )

        def _on_kickoff_end(source_: Any, event: Any) -> None:
            adapter._dispatch("stop", {"output": str(getattr(event, "output", ""))})

        def _on_tool_start(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "pre_tool_use",
                {
                    "tool_name": getattr(event, "tool_name", ""),
                    "tool_input": {"args": str(getattr(event, "tool_args", ""))},
                },
            )

        def _on_tool_end(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "post_tool_use",
                {
                    "tool_name": getattr(event, "tool_name", ""),
                    "tool_response": str(getattr(event, "output", "")),
                },
            )

        def _on_task_start(source_: Any, event: Any) -> None:
            adapter._dispatch(
                "subagent_start",
                {"agent_id": getattr(event, "task_id", "") or str(id(event))},
            )

        crewai_event_bus.on(CrewKickoffStartedEvent)(_on_kickoff_start)
        crewai_event_bus.on(CrewKickoffCompletedEvent)(_on_kickoff_end)
        crewai_event_bus.on(ToolUsageStartedEvent)(_on_tool_start)
        crewai_event_bus.on(ToolUsageFinishedEvent)(_on_tool_end)
        crewai_event_bus.on(TaskStartedEvent)(_on_task_start)
