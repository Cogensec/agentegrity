"""AWS Bedrock Agents adapter for agentegrity.

Bedrock Agents has two surfaces and we cover both:

* **Strands SDK** (``instrument_strands(agent)``). The AWS-blessed
  forward path for new code. Registers a :class:`HookProvider` that
  subscribes to ``BeforeInvocationEvent`` / ``AfterInvocationEvent``
  for session lifecycle and ``BeforeToolCallEvent`` /
  ``AfterToolCallEvent`` for tool lifecycle. This path supports real
  enforcement: when ``enforce=True`` and the integrity score's action
  is ``"block"``, the adapter writes ``event.cancel_tool`` so Strands
  refuses to execute the tool.

* **boto3 ``bedrock-agent-runtime``** (``wrap_client(client)``).
  Covers users calling ``bedrock-agent-runtime`` directly. Wraps the
  client's ``invoke_agent`` method so it forces ``enableTrace=True``
  (configurable via ``force_trace``), then transparently iterates the
  returned ``EventStream``, mapping each ``trace`` payload onto
  canonical events and re-yielding ``chunk`` events to the caller.
  Observation-only: trace events are post-hoc, so ``enforce=True``
  on this surface records the block decision but cannot actually
  prevent the tool call. The adapter warns at construction if both
  ``enforce=True`` and the boto3 path are used.

Event mapping (both paths converge on the same canonical events):

    Strands:
        BeforeInvocationEvent          ->  user_prompt_submit
        BeforeToolCallEvent            ->  pre_tool_use
        AfterToolCallEvent  (ok)       ->  post_tool_use
        AfterToolCallEvent  (exc)      ->  post_tool_use_failure
        AfterInvocationEvent           ->  stop

    boto3 TracePart variants:
        wrap_client.invoke_agent call  ->  user_prompt_submit
        orchestrationTrace.invocationInput.actionGroupInvocationInput
                                        ->  pre_tool_use
        orchestrationTrace.observation.actionGroupInvocationOutput
                                        ->  post_tool_use
        orchestrationTrace.invocationInput.agentCollaboratorInvocationInput
                                        ->  subagent_start
        orchestrationTrace.observation.agentCollaboratorInvocationOutput
                                        ->  subagent_stop
        failureTrace                    ->  post_tool_use_failure
        EventStream exhausted           ->  stop
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Any, Iterator

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.profile import AgentProfile

if TYPE_CHECKING:
    from strands.agent import Agent as StrandsAgent
    from strands.hooks import HookRegistry

logger = logging.getLogger("agentegrity.adapters.bedrock_agents")


class BedrockAgentsAdapter(_BaseAdapter):
    """Instruments Bedrock Agents via Strands hooks OR boto3 trace-stream wrapping."""

    _name = "bedrock_agents"

    def __init__(
        self,
        profile: AgentProfile,
        evaluator: IntegrityEvaluator | None = None,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> None:
        super().__init__(profile, evaluator, enforce, api_key)

    # --- v0.8 multi-agent topology helpers ---

    def _seed_topology_from_supervisor(self, supervisor_id: str) -> None:
        """Declare a HUB_SPOKE topology seeded with the supervisor only.

        Collaborators are appended incrementally as
        ``agentCollaboratorInvocationInput`` trace parts arrive.
        Called once at the start of each ``invoke_agent`` / Strands
        run. If the supervisor matches the current topology's leader,
        no-op (Bedrock doesn't reset across invocations).
        """
        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        current = self._buffer.topology
        if current is not None and current.leader() is not None:
            if current.leader().agent_id == supervisor_id:
                return  # same supervisor, keep topology

        supervisor = AgentMember(
            agent_id=supervisor_id,
            name=supervisor_id,
            role=AgentRole.LEADER,
            capabilities=("tool_use",),
        )
        topology = AgentTopology(
            kind=TopologyKind.HUB_SPOKE,
            members=(supervisor,),
            comm_channels=frozenset({"peer_messages"}),
        )
        self.set_topology(topology, my_role=AgentRole.LEADER)

    def _ensure_collaborator(self, collaborator_name: str) -> None:
        """Append a collaborator to the current topology if absent.

        Each Bedrock collaborator observed in the trace stream
        becomes an ``AgentMember`` with role MEMBER under the
        supervisor. Triggers a ``topology_change`` event so the
        chain commits to the new member.
        """
        from agentegrity.core.topology import AgentMember, AgentRole

        topology = self._buffer.topology
        if topology is None or not collaborator_name:
            return
        if topology.member(collaborator_name) is not None:
            return
        leader = topology.leader()
        parent_id = leader.agent_id if leader is not None else None
        new_topology = topology.with_member(AgentMember(
            agent_id=collaborator_name,
            name=collaborator_name,
            role=AgentRole.MEMBER,
            parent_id=parent_id,
            capabilities=("tool_use",),
        ))
        self.set_topology(new_topology, my_role=AgentRole.LEADER)

    # --- Strands SDK path ---

    def instrument_strands(self, agent: StrandsAgent) -> StrandsAgent:
        """Register agentegrity hooks on a Strands :class:`Agent`.

        v0.8: also seeds a HUB_SPOKE topology with the Strands agent
        as the supervisor; collaborators discovered through the trace
        stream (when the Strands agent invokes one) grow the topology
        incrementally via ``topology_change`` events.

        Subscribes to invocation + tool lifecycle events. Tool-call hooks
        run synchronously enough that ``enforce=True`` can deny a tool
        before Strands executes it (via ``event.cancel_tool``).
        """
        try:
            agent.hooks.add_hook(_StrandsHookProvider(self))
        except AttributeError as exc:
            raise TypeError(
                "instrument_strands expected a strands.Agent with a .hooks "
                f"HookRegistry; got {type(agent).__name__}: {exc}"
            ) from None
        # v0.8: seed topology with the Strands agent as supervisor.
        supervisor_id = str(getattr(agent, "name", None) or id(agent))
        self._seed_topology_from_supervisor(supervisor_id)
        return agent

    # --- boto3 trace-stream path ---

    def wrap_client(self, client: Any, *, force_trace: bool = True) -> Any:
        """Wrap a ``bedrock-agent-runtime`` boto3 client.

        Replaces ``client.invoke_agent`` with a version that forces
        ``enableTrace=True`` (unless ``force_trace=False``) and iterates
        the returned ``EventStream`` through our trace-to-canonical-event
        mapper. The wrapped client still returns a dict whose
        ``completion`` is an iterator the caller can consume normally —
        only the trace events are intercepted; ``chunk`` / ``files`` /
        ``returnControl`` / exception events pass through.

        Args:
            client: A boto3 ``bedrock-agent-runtime`` client.
            force_trace: When True (default) injects ``enableTrace=True``
                on every ``invoke_agent`` call. Required for the adapter
                to see anything — set False only if you wire tracing on
                yourself, or if you want this adapter to be a no-op for
                latency reasons.

        Returns:
            The same client, with ``invoke_agent`` replaced. Returning the
            client preserves the user's calling convention.
        """
        if self._enforce:
            warnings.warn(
                "BedrockAgentsAdapter.wrap_client is observation-only: the "
                "boto3 trace stream fires after tool execution, so "
                "enforce=True records block decisions but cannot prevent "
                "the tool from running. For real enforcement, use "
                "instrument_strands() on a Strands Agent.",
                UserWarning,
                stacklevel=2,
            )

        adapter = self
        original_invoke = client.invoke_agent

        def patched_invoke_agent(**kwargs: Any) -> dict[str, Any]:
            if force_trace and "enableTrace" not in kwargs:
                kwargs["enableTrace"] = True
            elif not force_trace and not kwargs.get("enableTrace"):
                logger.warning(
                    "bedrock_agents wrap_client called with force_trace=False "
                    "and enableTrace unset; no agentegrity events will fire "
                    "from this invocation."
                )

            # v0.8: declare an initial HUB_SPOKE topology with just the
            # supervisor agent. Collaborators land via topology_change
            # events as agentCollaboratorInvocationInput trace parts
            # arrive (see _process_trace_part).
            agent_id_arg = str(
                kwargs.get("agentId")
                or kwargs.get("agentAliasId")
                or "bedrock-supervisor"
            )
            adapter._seed_topology_from_supervisor(agent_id_arg)

            adapter._dispatch(
                "user_prompt_submit",
                {"prompt": str(kwargs.get("inputText", ""))},
            )

            response: dict[str, Any] = original_invoke(**kwargs)
            completion = response.get("completion")
            if completion is not None:
                response["completion"] = _wrap_event_stream(adapter, completion)
            return response

        client.invoke_agent = patched_invoke_agent
        return client


# --- Strands hook provider implementation ---


class _StrandsHookProvider:
    """Bridges Strands hook events to the adapter's canonical event stream."""

    def __init__(self, adapter: BedrockAgentsAdapter) -> None:
        self._adapter = adapter

    def register_hooks(self, registry: HookRegistry, **_: Any) -> None:
        try:
            from strands.hooks.events import (
                AfterInvocationEvent,
                AfterToolCallEvent,
                BeforeInvocationEvent,
                BeforeToolCallEvent,
            )
        except ImportError:
            raise ImportError(
                "strands-agents is required for instrument_strands. "
                "Install it with: pip install agentegrity[bedrock-agents]"
            ) from None

        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)

    # Sync callbacks for the lifecycle events that don't need enforcement —
    # _dispatch is fire-and-forget which is fine for observation.
    def _on_before_invocation(self, event: Any) -> None:
        messages = getattr(event, "messages", None)
        prompt = _extract_prompt(messages)
        self._adapter._dispatch("user_prompt_submit", {"prompt": prompt})

    def _on_after_invocation(self, event: Any) -> None:
        result = getattr(event, "result", None)
        self._adapter._dispatch(
            "stop", {"output": str(result) if result is not None else ""}
        )

    # Async tool-call callbacks. We need the evaluator's verdict synchronously
    # enough to write event.cancel_tool, so we await on_event and inspect the
    # block decision dict that _handle_pre_tool_use returns.
    async def _on_before_tool_call(self, event: Any) -> None:
        tool_use = getattr(event, "tool_use", {}) or {}
        result = await self._adapter.on_event(
            "pre_tool_use",
            {
                "tool_name": tool_use.get("name", ""),
                "tool_input": dict(tool_use.get("input", {})) if tool_use.get("input") else {},
            },
        )
        decision = (
            result.get("hookSpecificOutput", {}).get("permissionDecision")
            if result
            else None
        )
        if decision == "deny":
            reason = result["hookSpecificOutput"].get(
                "permissionDecisionReason", "blocked by agentegrity"
            )
            try:
                event.cancel_tool = reason
            except AttributeError:
                # Defensive: Strands restricts writes via __setattr__. If a
                # future version locks cancel_tool down, log instead of
                # crashing the agent run.
                logger.warning(
                    "bedrock_agents: tried to cancel tool '%s' but "
                    "event.cancel_tool is not writable on this Strands version",
                    tool_use.get("name", ""),
                )

    async def _on_after_tool_call(self, event: Any) -> None:
        tool_use = getattr(event, "tool_use", {}) or {}
        exception = getattr(event, "exception", None)
        tool_name = tool_use.get("name", "")
        if exception is not None:
            await self._adapter.on_event(
                "post_tool_use_failure",
                {"tool_name": tool_name, "error": str(exception)},
            )
        else:
            result = getattr(event, "result", None)
            await self._adapter.on_event(
                "post_tool_use",
                {"tool_name": tool_name, "tool_response": str(result)},
            )


def _extract_prompt(messages: Any) -> str:
    """Pull the most recent user-message text from a Strands messages list."""
    if not messages:
        return ""
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and "text" in block:
                return str(block["text"])
    return str(content) if content is not None else ""


# --- boto3 EventStream wrapping ---


def _wrap_event_stream(adapter: BedrockAgentsAdapter, stream: Any) -> Iterator[dict[str, Any]]:
    """Iterate a Bedrock Agents EventStream, mapping trace events to canonical."""
    completed = False
    try:
        for raw_event in stream:
            _handle_stream_event(adapter, raw_event)
            yield raw_event
        completed = True
    finally:
        # Always fire stop so partial streams (caller stops iterating, network
        # error, etc.) still close the session in the attestation chain.
        if not completed:
            adapter._dispatch("stop", {"reason": "stream_terminated_early"})
        else:
            adapter._dispatch("stop", {})


def _handle_stream_event(adapter: BedrockAgentsAdapter, raw_event: dict[str, Any]) -> None:
    """Map one EventStream variant to a canonical event (or skip)."""
    trace_part = raw_event.get("trace")
    if not trace_part:
        return  # chunk / files / returnControl / exception variants — caller handles
    trace = trace_part.get("trace") or {}

    failure = trace.get("failureTrace")
    if failure:
        adapter._dispatch(
            "post_tool_use_failure",
            {
                "tool_name": "",
                "error": failure.get("failureReason", "")
                or str(failure.get("failureCode", "")),
            },
        )
        return

    orch = trace.get("orchestrationTrace") or {}
    inv_in = orch.get("invocationInput") or {}
    obs = orch.get("observation") or {}

    action_in = inv_in.get("actionGroupInvocationInput")
    if action_in:
        adapter._dispatch(
            "pre_tool_use",
            {
                "tool_name": action_in.get("function")
                or action_in.get("apiPath", ""),
                "tool_input": {
                    "action_group": action_in.get("actionGroupName", ""),
                    "parameters": action_in.get("parameters", []),
                },
            },
        )
        return

    collab_in = inv_in.get("agentCollaboratorInvocationInput")
    if collab_in:
        collaborator_name = collab_in.get("agentCollaboratorName", "")
        # v0.8: incrementally grow the topology so the chain commits
        # to which collaborators participated under this supervisor.
        if collaborator_name:
            adapter._ensure_collaborator(collaborator_name)
        adapter._dispatch(
            "subagent_start",
            {"agent_id": collaborator_name},
        )
        return

    action_out = obs.get("actionGroupInvocationOutput")
    if action_out:
        adapter._dispatch(
            "post_tool_use",
            {"tool_name": "", "tool_response": action_out.get("text", "")},
        )
        return

    collab_out = obs.get("agentCollaboratorInvocationOutput")
    if collab_out:
        adapter._dispatch(
            "subagent_stop",
            {"agent_id": collab_out.get("agentCollaboratorName", "")},
        )
        return
