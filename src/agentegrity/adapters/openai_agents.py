"""
OpenAI Agents SDK adapter for agentegrity.

Instruments agents built on the OpenAI Agents SDK (``agents`` package)
by subclassing ``RunHooks`` and forwarding to the shared ``_BaseAdapter``
event dispatcher.

Event mapping:
    on_agent_start     -> user_prompt_submit
    on_tool_start      -> pre_tool_use
    on_tool_end        -> post_tool_use
    on_handoff         -> subagent_start
    on_agent_end       -> stop

Usage:
    from agents import Agent, Runner
    from agentegrity.openai_agents import run_hooks, report

    agent = Agent(name="my-agent", ...)
    await Runner.run(agent, input="...", hooks=run_hooks())
    print(report())
"""

from __future__ import annotations

import logging
from typing import Any

from agentegrity.adapters.base import _BaseAdapter

logger = logging.getLogger("agentegrity.adapters.openai_agents")


class OpenAIAgentsAdapter(_BaseAdapter):
    """Instruments an OpenAI Agents SDK run with agentegrity evaluation."""

    _name = "openai_agents"

    def create_run_hooks(self) -> Any:
        """Return a ``RunHooks`` subclass instance bound to this adapter.

        Imports ``RunHooks`` at call time so the adapter module can be
        imported without the ``openai-agents`` package installed.
        """
        try:
            from agents import RunHooks
        except ImportError:
            raise ImportError(
                "openai-agents is required for the OpenAI Agents adapter. "
                "Install it with: pip install agentegrity[openai-agents]"
            ) from None

        adapter = self

        class _AgentegrityRunHooks(RunHooks):  # type: ignore[misc, unused-ignore]
            async def on_agent_start(
                self, context: Any, agent: Any
            ) -> None:
                prompt = ""
                try:
                    prompt = str(getattr(context, "input", "") or "")
                except Exception:
                    pass
                await adapter.on_event("user_prompt_submit", {"prompt": prompt})

            async def on_agent_end(
                self, context: Any, agent: Any, output: Any
            ) -> None:
                await adapter.on_event("stop", {"output": str(output)})

            async def on_tool_start(
                self, context: Any, agent: Any, tool: Any
            ) -> None:
                tool_name = getattr(tool, "name", str(tool))
                await adapter.on_event(
                    "pre_tool_use",
                    {"tool_name": tool_name, "tool_input": {}},
                )

            async def on_tool_end(
                self, context: Any, agent: Any, tool: Any, result: Any
            ) -> None:
                tool_name = getattr(tool, "name", str(tool))
                await adapter.on_event(
                    "post_tool_use",
                    {"tool_name": tool_name, "tool_response": str(result)},
                )

            async def on_handoff(
                self, context: Any, from_agent: Any, to_agent: Any
            ) -> None:
                await adapter.on_event(
                    "subagent_start",
                    {"agent_id": getattr(to_agent, "name", str(to_agent))},
                )

        return _AgentegrityRunHooks()
