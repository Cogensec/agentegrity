"""
Google Agent Development Kit (ADK) adapter for agentegrity.

Instruments ``google.adk`` ``LlmAgent``/``Agent`` instances by attaching
the six callback hooks ADK exposes on ``Agent``:

    before_agent_callback  -> user_prompt_submit
    after_agent_callback   -> stop
    before_tool_callback   -> pre_tool_use
    after_tool_callback    -> post_tool_use
    (before/after_model_callback accumulate reasoning-chain context)

Sub-agent handoffs through ``AgentTool`` fire ``before_agent_callback``
with a non-root invocation context; we map those to ``subagent_start``.

Limitation: this adapter is fundamentally observation-only. ADK's
``before_*`` callbacks expose no return-value or exception-signaling
mechanism the runtime acts on to veto a tool call, so ``enforce=True``
records block decisions in the attestation chain but cannot prevent
the call. The adapter warns at construction when ``enforce=True``.

Usage:
    from google.adk.agents import LlmAgent
    from agentegrity.google_adk import instrument, report

    agent = LlmAgent(name="my-agent", ...)
    instrument(agent)
    # run via google.adk.runners.Runner
    print(report())
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.profile import AgentProfile

logger = logging.getLogger("agentegrity.adapters.google_adk")


class GoogleADKAdapter(_BaseAdapter):
    """Instruments a Google ADK agent with agentegrity evaluation."""

    _name = "google_adk"

    def __init__(
        self,
        profile: AgentProfile,
        evaluator: IntegrityEvaluator | None = None,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> None:
        super().__init__(profile, evaluator, enforce, api_key)
        if enforce:
            warnings.warn(
                "GoogleADKAdapter is observation-only: ADK before_* callbacks "
                "expose no return-value or exception-signaling mechanism the "
                "runtime acts on, so enforce=True records block decisions in "
                "the attestation chain but cannot prevent tool calls. For "
                "enforcement, use a framework with a blocking pre-tool hook.",
                UserWarning,
                stacklevel=2,
            )

    def instrument(self, agent: Any) -> Any:
        """Attach agentegrity callbacks to a Google ADK agent.

        Mutates the passed agent's ``before_*`` / ``after_*`` callback
        attributes and returns it for chaining. If the agent already has
        user-supplied callbacks, agentegrity chains onto them — original
        callbacks still fire.
        """
        adapter = self

        def _wrap(existing: Any, fn: Any) -> Any:
            if existing is None:
                return fn

            def _chained(*args: Any, **kwargs: Any) -> Any:
                try:
                    fn(*args, **kwargs)
                except Exception as exc:
                    logger.warning("google_adk agentegrity callback failed: %s", exc)
                return existing(*args, **kwargs)

            return _chained

        def _before_agent(callback_context: Any) -> None:
            parent = getattr(callback_context, "parent", None)
            if parent is None:
                prompt = str(getattr(callback_context, "user_content", "") or "")
                adapter._dispatch("user_prompt_submit", {"prompt": prompt})
            else:
                adapter._dispatch(
                    "subagent_start",
                    {"agent_id": getattr(callback_context, "agent_name", "") or ""},
                )

        def _after_agent(callback_context: Any) -> None:
            parent = getattr(callback_context, "parent", None)
            if parent is None:
                adapter._dispatch("stop", {})

        def _before_tool(tool: Any, args: Any, tool_context: Any) -> None:
            tool_name = getattr(tool, "name", str(tool))
            adapter._dispatch(
                "pre_tool_use",
                {"tool_name": tool_name, "tool_input": dict(args) if args else {}},
            )

        def _after_tool(tool: Any, args: Any, tool_context: Any, tool_response: Any) -> None:
            tool_name = getattr(tool, "name", str(tool))
            adapter._dispatch(
                "post_tool_use",
                {"tool_name": tool_name, "tool_response": str(tool_response)},
            )

        try:
            agent.before_agent_callback = _wrap(
                getattr(agent, "before_agent_callback", None), _before_agent
            )
            agent.after_agent_callback = _wrap(
                getattr(agent, "after_agent_callback", None), _after_agent
            )
            agent.before_tool_callback = _wrap(
                getattr(agent, "before_tool_callback", None), _before_tool
            )
            agent.after_tool_callback = _wrap(
                getattr(agent, "after_tool_callback", None), _after_tool
            )
        except Exception as exc:
            raise ImportError(
                "google-adk is required for the Google ADK adapter, or the "
                "passed object is not a Google ADK Agent. "
                "Install it with: pip install agentegrity[google-adk]"
            ) from exc

        # v0.8: if the agent has sub_agents (SequentialAgent /
        # ParallelAgent / LoopAgent), declare a HIERARCHICAL_DAG
        # topology. Plain Agent without sub_agents stays single-agent.
        self._maybe_declare_workflow_topology(agent)
        return agent

    def _maybe_declare_workflow_topology(self, agent: Any) -> None:
        """Walk a Google ADK workflow agent's sub_agents and declare a
        HIERARCHICAL_DAG topology.

        ``SequentialAgent`` / ``ParallelAgent`` / ``LoopAgent`` all
        expose ``sub_agents``. A plain ``Agent`` does not — that case
        is correctly single-agent and we skip declaration.
        """
        sub_agents = getattr(agent, "sub_agents", None)
        if not sub_agents:
            return

        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        supervisor_id = str(getattr(agent, "name", None) or id(agent))
        members: list[AgentMember] = [AgentMember(
            agent_id=supervisor_id,
            name=supervisor_id,
            role=AgentRole.SUPERVISOR,
            capabilities=("tool_use",),
        )]
        for sub in sub_agents:
            sub_id = str(getattr(sub, "name", None) or id(sub))
            members.append(AgentMember(
                agent_id=sub_id,
                name=sub_id,
                role=AgentRole.WORKER,
                parent_id=supervisor_id,
                capabilities=("tool_use",),
            ))

        topology = AgentTopology(
            kind=TopologyKind.HIERARCHICAL_DAG,
            members=tuple(members),
            comm_channels=frozenset(),
        )
        self.set_topology(topology, my_role=AgentRole.SUPERVISOR)
