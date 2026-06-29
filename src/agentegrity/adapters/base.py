"""
Base adapter protocol and shared base class for framework integrations.

All framework adapters (Claude Agent SDK, LangChain/LangGraph, OpenAI
Agents SDK, CrewAI, Google ADK) implement the ``FrameworkAdapter``
Protocol. To avoid re-writing the event-handling / evaluation /
attestation plumbing in every adapter, they inherit from
``_BaseAdapter``, which owns:

- ``_ContextBuffer`` accumulation
- ``on_event`` dispatch to framework-agnostic handlers
- ``_run_evaluation`` + attestation record append
- ``get_collected_context`` / ``get_summary`` / ``events`` /
  ``attestation_chain`` / ``evaluation_count`` properties

Subclasses only need to override ``name`` and add framework-specific
entry points (hook registration / callback attachment / event-bus
subscription). The hook surface is identical across every framework
because each one ultimately fires the same seven event types:

    pre_tool_use / post_tool_use / post_tool_use_failure
    user_prompt_submit / stop
    subagent_start / subagent_stop
    pre_compact
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from agentegrity.core.attestation import (
    AttestationChain,
    build_attestation_record,
)
from agentegrity.core.decision import (
    DecisionInput,
    DecisionRecord,
    RejectedAlternative,
    build_decision_record,
)
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore
from agentegrity.core.profile import AgentProfile

logger = logging.getLogger("agentegrity.adapters")


class SessionExporter(Protocol):
    """Protocol for exporters that receive live session data.

    Exporters subscribe to an adapter via ``register_exporter`` and
    receive three kinds of callback during the adapter's lifetime:

    - ``on_session_start`` fires once, just before the first event is
      emitted, with the adapter's session id, adapter name, and a
      JSON-serializable dict snapshot of the agent profile.
    - ``on_event`` fires for every ``FrameworkEvent`` the adapter
      emits, with ``event.to_dict()``.
    - ``on_session_end`` fires from ``close()`` / ``__aexit__``, with
      the adapter's ``get_summary()``.

    All methods are async. Exceptions raised by an exporter are caught
    and logged by ``_BaseAdapter._notify_exporters`` — an exporter must
    never be able to break the instrumented agent.
    """

    async def on_session_start(
        self,
        session_id: str,
        adapter_name: str,
        profile: dict[str, Any],
    ) -> None: ...

    async def on_event(
        self, session_id: str, event: dict[str, Any]
    ) -> None: ...

    async def on_session_end(
        self, session_id: str, summary: dict[str, Any]
    ) -> None: ...


class FrameworkAdapter(Protocol):
    """Protocol that all framework adapters must implement."""

    @property
    def name(self) -> str: ...

    @property
    def profile(self) -> AgentProfile: ...

    @property
    def events(self) -> list[FrameworkEvent]: ...

    def get_collected_context(self) -> dict[str, Any]: ...

    async def on_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass
class FrameworkEvent:
    """A structured event emitted by a framework adapter."""

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
                self.evaluation_result.to_dict() if self.evaluation_result else None
            ),
        }


@dataclass
class _ContextBuffer:
    """Internal buffer accumulating runtime context from framework hooks.

    Multi-agent fields (peer_messages, shared_memory, broadcast_messages,
    topology, my_role, peer_attestations, peer_score_history,
    subagent_starts_seen) are populated by team-aware adapters. They
    flow into ``to_evaluation_context()`` under a ``topology_context``
    key so the four existing layers can scan them under their own
    properties.
    """

    inputs: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    tool_failures: list[dict[str, Any]] = field(default_factory=list)
    tool_usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    action_distribution: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    reasoning_chain: list[str] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)
    # Multi-agent (v0.8) — set/populated by team-aware adapters.
    topology: Any = None  # AgentTopology | None (Any to avoid the import cycle)
    my_role: Any = None  # AgentRole | None
    peer_messages: list[dict[str, Any]] = field(default_factory=list)
    shared_memory: list[dict[str, Any]] = field(default_factory=list)
    broadcast_messages: list[dict[str, Any]] = field(default_factory=list)
    peer_attestations: list[dict[str, Any]] = field(default_factory=list)
    peer_score_history: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    subagent_starts_seen: set[str] = field(default_factory=set)
    tasks: list[dict[str, Any]] = field(default_factory=list)

    def to_evaluation_context(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "input": self.inputs[-1] if self.inputs else "",
            "tool_outputs": self.tool_outputs,
            "reasoning_chain": self.reasoning_chain,
            "goals": [],
            "instructions": [],
            "memory_reads": [],
            "action_distribution": dict(self.action_distribution),
            "tool_usage": dict(self.tool_usage),
            "action": (
                self.tool_calls[-1] if self.tool_calls else {"type": "respond"}
            ),
            "peer_messages": self.peer_messages,
        }
        if (
            self.topology is not None
            or self.peer_messages
            or self.shared_memory
            or self.broadcast_messages
        ):
            base["topology_context"] = {
                "topology": (
                    self.topology.to_dict() if self.topology is not None else None
                ),
                "role": (
                    self.my_role.value
                    if self.my_role is not None
                    else None
                ),
                "peer_messages": list(self.peer_messages),
                "shared_memory": list(self.shared_memory),
                "broadcast_messages": list(self.broadcast_messages),
                "peer_attestations": list(self.peer_attestations),
                "peer_score_history": dict(self.peer_score_history),
            }
        return base


async def _safe_await(coro: Any, method_name: str) -> None:
    """Await a coroutine, logging and swallowing any exception."""
    try:
        await coro
    except Exception as exc:
        logger.warning("exporter %s failed: %s", method_name, exc)


class _BaseAdapter:
    """Shared machinery for framework adapters.

    Subclasses set ``_name`` (class attribute) and may add
    framework-specific entry points. The event-handling, evaluation,
    and attestation layer is framework-agnostic and lives here.
    """

    _name: str = "base"

    def __init__(
        self,
        profile: AgentProfile,
        evaluator: IntegrityEvaluator | None = None,
        enforce: bool = False,
        api_key: str | None = None,
        signing_key: Any | None = None,
        approval_handler: Callable[..., bool] | None = None,
    ) -> None:
        self._profile = profile
        self._enforce = enforce
        self._api_key = api_key
        self._signing_key = signing_key
        # Called when enforce=True and an action escalates (governance
        # require-approval, cortical drift, recovery chain-tamper).
        # Returns True to approve (allow), False to deny. Absent ⇒
        # escalations fail closed (deny), so "require approval" is not
        # silently advisory under enforcement.
        self._approval_handler = approval_handler
        self._buffer = _ContextBuffer()
        self._events: list[FrameworkEvent] = []
        self._chain = AttestationChain()
        self._evaluation_count = 0
        self._session_id = uuid4().hex
        self._exporters: list[SessionExporter] = []
        self._session_started = False
        self._session_ended = False
        self._pending_topology_change: Any = None  # TopologyChange | None

        if evaluator is not None:
            self._evaluator = evaluator
        else:
            from agentegrity.layers import default_layers

            self._evaluator = IntegrityEvaluator(layers=default_layers())

    @property
    def name(self) -> str:
        return self._name

    @property
    def profile(self) -> AgentProfile:
        return self._profile

    @property
    def events(self) -> list[FrameworkEvent]:
        return list(self._events)

    @property
    def attestation_chain(self) -> AttestationChain:
        return self._chain

    @property
    def evaluation_count(self) -> int:
        return self._evaluation_count

    @property
    def session_id(self) -> str:
        return self._session_id

    def register_exporter(self, exporter: SessionExporter) -> None:
        """Register a :class:`SessionExporter` to receive live session data.

        Multiple exporters may be registered; each receives every event.
        Registration is idempotent — the same exporter instance won't
        be added twice.
        """
        if exporter not in self._exporters:
            self._exporters.append(exporter)

    def _notify_exporters(self, method_name: str, *args: Any) -> None:
        """Fan out a callback to every registered exporter, fail-open.

        Runs each exporter coroutine on the current event loop via
        ``asyncio.ensure_future`` when a loop is running, otherwise via
        ``asyncio.run``. Exporter exceptions are logged but never
        propagated — instrumentation must not break the agent.
        """
        if not self._exporters:
            return
        for exporter in self._exporters:
            coro_factory = getattr(exporter, method_name, None)
            if coro_factory is None:
                continue
            try:
                coro = coro_factory(*args)
            except Exception as exc:
                logger.warning(
                    "exporter %s.%s raised synchronously: %s",
                    type(exporter).__name__,
                    method_name,
                    exc,
                )
                continue
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_safe_await(coro, method_name))
                    continue
            except RuntimeError:
                pass
            try:
                asyncio.run(_safe_await(coro, method_name))
            except Exception as exc:
                logger.warning(
                    "exporter %s.%s failed: %s",
                    type(exporter).__name__,
                    method_name,
                    exc,
                )

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        """Evaluate an event from a synchronous, fire-and-forget caller.

        Observation-only hook surfaces fire from synchronous contexts
        and don't act on the decision. They call this shim, which runs
        the synchronous evaluation inline and discards the result.
        Failures are logged and swallowed so a hook error never breaks
        the instrumented agent.
        """
        try:
            self._evaluate_sync(event_type, data)
        except Exception as exc:
            logger.warning("%s dispatch %s failed: %s", self.name, event_type, exc)

    def _maybe_start_session(self) -> None:
        if self._session_started or not self._exporters:
            self._session_started = True
            return
        self._session_started = True
        self._notify_exporters(
            "on_session_start",
            self._session_id,
            self.name,
            self._profile.to_dict(),
        )

    def close(self) -> None:
        """Fire ``on_session_end`` on all registered exporters.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._session_ended or not self._exporters:
            self._session_ended = True
            return
        self._session_ended = True
        self._notify_exporters(
            "on_session_end",
            self._session_id,
            self.get_summary(),
        )

    async def __aenter__(self) -> _BaseAdapter:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def get_collected_context(self) -> dict[str, Any]:
        return self._buffer.to_evaluation_context()

    def _emit_event(
        self,
        event_type: str,
        data: dict[str, Any],
        score: IntegrityScore | None = None,
    ) -> FrameworkEvent:
        event = FrameworkEvent(
            event_type=event_type,
            adapter_name=self.name,
            data=data,
            evaluation_result=score,
        )
        self._events.append(event)
        if self._exporters:
            self._maybe_start_session()
            self._notify_exporters(
                "on_event", self._session_id, event.to_dict()
            )
        return event

    def _run_evaluation(
        self, context: dict[str, Any] | None = None
    ) -> IntegrityScore:
        ctx = context or self._buffer.to_evaluation_context()
        score = self._evaluator.evaluate(self._profile, ctx)
        self._evaluation_count += 1

        prev_hash = self._chain.latest.content_hash if self._chain.latest else None
        # Consume the pending topology change (if any) so it lands on
        # exactly one attestation. Topology itself stays sticky — every
        # subsequent attestation carries the topology Evidence until
        # the topology is replaced.
        pending_change = self._pending_topology_change
        self._pending_topology_change = None
        record = build_attestation_record(
            self._profile,
            score,
            previous_record_hash=prev_hash,
            signing_key=self._signing_key,
            recent_decisions=self._decisions_since_last_attestation(),
            topology=self._buffer.topology,
            topology_change=pending_change,
        )
        self._chain.append(record)
        return score

    def set_topology(
        self,
        topology: Any,  # AgentTopology
        my_role: Any = None,  # AgentRole | None
    ) -> None:
        """Declare or update the in-process multi-agent topology this
        adapter participates in.

        Called by team-aware adapters at instrument time (Agno
        ``instrument_team``, CrewAI ``instrument`` over a Crew, etc.)
        and again on any structural mutation. The first call emits a
        ``topology_declared`` event; subsequent calls emit
        ``topology_change`` with a :class:`TopologyChange` diff. Both
        kinds of event trigger an attestation so the chain commits to
        the topology via ``Evidence(evidence_type="topology")``.
        """
        previous = self._buffer.topology
        self._buffer.topology = topology
        self._buffer.my_role = my_role

        if previous is None:
            self._dispatch(
                "topology_declared",
                {"topology": topology.to_dict()},
            )
            return

        if previous.content_hash() == topology.content_hash():
            # No structural change — nothing to attest.
            return

        from agentegrity.core.topology import TopologyChange

        change = TopologyChange.between(previous, topology)
        self._pending_topology_change = change
        self._dispatch(
            "topology_change",
            {
                "change": change.to_dict(),
                "topology": topology.to_dict(),
            },
        )

    def _decisions_since_last_attestation(self) -> list[DecisionRecord]:
        """Return the trailing run of :class:`DecisionRecord`\\s since
        the most recent attestation (or since the start of the chain
        if none yet)."""
        recent: list[DecisionRecord] = []
        for r in reversed(self._chain.records):
            if r.record_kind == "attestation":
                break
            if isinstance(r, DecisionRecord):
                recent.append(r)
        recent.reverse()
        return recent

    def record_decision(
        self,
        decision_point: str,
        candidate_action: dict[str, Any],
        *,
        reasoning_chain: list[str] | None = None,
        rejected_alternatives: list[RejectedAlternative] | None = None,
        decision_inputs: list[DecisionInput] | None = None,
        goal_state: list[str] | None = None,
    ) -> DecisionRecord | None:
        """Build, sign (if a key is configured), and append a :class:`DecisionRecord`.

        Fails open: on any exception the function logs a warning, emits
        a structured ``capture_failure`` :class:`FrameworkEvent` so the
        gap is queryable downstream, and returns ``None``. The handler
        that called it continues normally — capture must never break
        the instrumented agent.
        """
        try:
            prev_hash = (
                self._chain.latest.content_hash if self._chain.latest else None
            )
            record = build_decision_record(
                agent_id=self._profile.agent_id,
                decision_point=decision_point,
                candidate_action=candidate_action,
                reasoning_chain=reasoning_chain,
                rejected_alternatives=rejected_alternatives,
                decision_inputs=decision_inputs,
                goal_state=goal_state,
                previous_record_hash=prev_hash,
                signing_key=self._signing_key,
            )
            self._chain.append(record)
            return record
        except Exception as exc:
            logger.warning(
                "%s decision capture failed at %s: %s",
                self.name, decision_point, exc, exc_info=True,
            )
            self._emit_event(
                "capture_failure",
                {
                    "decision_point": decision_point,
                    "exception_class": type(exc).__name__,
                    "summary": str(exc)[:200],
                },
            )
            return None

    def _collect_decision_inputs(self) -> list[DecisionInput]:
        """Build :class:`DecisionInput` entries from the buffer's populated
        channels. Today: latest user prompt + latest tool output. Other
        channels (memory_reads, goals, instructions) are reserved for
        adapters that populate them in the future.
        """
        inputs: list[DecisionInput] = []
        if self._buffer.inputs:
            latest = self._buffer.inputs[-1]
            inputs.append(DecisionInput(
                channel="user_prompt",
                content_hash=hashlib.sha256(latest.encode()).hexdigest(),
                summary=latest[:120],
            ))
        if self._buffer.tool_outputs:
            latest_out = self._buffer.tool_outputs[-1]
            output_str = str(latest_out.get("output", ""))
            inputs.append(DecisionInput(
                channel="tool_output",
                content_hash=hashlib.sha256(output_str.encode()).hexdigest(),
                summary=f"{latest_out.get('tool', '')}: {output_str[:80]}",
            ))
        return inputs

    async def on_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Async entry point required by the ``FrameworkAdapter`` Protocol.

        The handlers do no I/O, so the real work is synchronous. This
        is a thin wrapper over ``_evaluate_sync`` so async callbacks can
        ``await on_event(...)`` while sync callbacks call
        ``_evaluate_sync(...)`` directly.
        """
        return self._evaluate_sync(event_type, event_data)

    def _evaluate_sync(
        self, event_type: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Evaluate an event synchronously and return the decision dict.

        Returns the same shape as ``_handle_pre_tool_use``'s deny path
        (``{"hookSpecificOutput": {...}}``) when enforcement blocks, or
        an empty dict otherwise. Sync hook surfaces that can enforce
        (raise to halt the run) call this and act on the result.
        """
        handlers = {
            "pre_tool_use": self._handle_pre_tool_use,
            "post_tool_use": self._handle_post_tool_use,
            "post_tool_use_failure": self._handle_post_tool_use_failure,
            "user_prompt_submit": self._handle_user_prompt_submit,
            "stop": self._handle_stop,
            "subagent_start": self._handle_subagent_start,
            "subagent_stop": self._handle_subagent_stop,
            "pre_compact": self._handle_pre_compact,
            # Multi-agent (v0.8)
            "topology_declared": self._handle_topology_declared,
            "topology_change": self._handle_topology_change,
            "peer_message": self._handle_peer_message,
            "shared_memory_write": self._handle_shared_memory_write,
            "broadcast": self._handle_broadcast,
            "task_started": self._handle_task_started,
        }
        handler = handlers.get(event_type)
        if handler:
            try:
                return handler(event_data)
            except Exception as exc:
                logger.warning(
                    "%s handler %s failed: %s",
                    self.name,
                    event_type,
                    exc,
                    exc_info=True,
                )
        return {}

    # --- Enforcement ---

    def _deny_payload(self, reason: str) -> dict[str, Any]:
        """Build the hook deny payload from a human-readable reason."""
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def _enforcement_decision(
        self, score: Any, candidate_action: dict[str, Any]
    ) -> dict[str, Any]:
        """Decide whether enforcement blocks this action.

        Returns a deny hook payload when the action is blocked, else an
        empty dict. No-op when ``enforce`` is off.

        Under enforcement:

        * ``block`` always denies (never approvable).
        * ``escalate`` denies UNLESS an ``approval_handler`` is
          configured and approves it. This is the fix for the
          "require approval is silently advisory" gap: every escalate
          source (governance require-approval, cortical drift, recovery
          chain-tamper) now fails closed under enforcement instead of
          proceeding. A raising handler is treated as a denial.
        """
        if not self._enforce:
            return {}
        action = score.action
        if action == "block":
            return self._deny_payload(
                f"Agentegrity integrity score {score.composite:.3f} "
                f"triggered block action"
            )
        if action == "escalate":
            if self._approval_handler is not None:
                try:
                    approved = bool(
                        self._approval_handler(
                            self._profile, score, candidate_action
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "approval_handler raised (%s); denying fail-closed", exc
                    )
                    approved = False
                if approved:
                    return {}
            return self._deny_payload(
                f"Agentegrity integrity score {score.composite:.3f} "
                f"triggered escalate action without approval"
            )
        return {}

    # --- Framework-agnostic event handlers ---

    def _handle_pre_tool_use(self, data: dict[str, Any]) -> dict[str, Any]:
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        self._buffer.tool_calls.append(
            {"tool": tool_name, "type": "tool_call", **tool_input}
        )
        self._buffer.tool_usage[tool_name] += 1
        self._buffer.action_distribution["tool_call"] += 1

        score = self._run_evaluation()
        self.record_decision(
            decision_point="pre_tool_use",
            candidate_action={
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": tool_input,
            },
            decision_inputs=self._collect_decision_inputs(),
        )
        self._emit_event("pre_tool_use", data, score)

        return self._enforcement_decision(
            score,
            {
                "type": "tool_call",
                "tool_name": tool_name,
                "arguments": tool_input,
            },
        )

    def _handle_post_tool_use(self, data: dict[str, Any]) -> dict[str, Any]:
        tool_response = data.get("tool_response", "")
        self._buffer.tool_outputs.append(
            {"tool": data.get("tool_name", ""), "output": tool_response}
        )
        score = self._run_evaluation()
        self._emit_event("post_tool_use", data, score)
        return {}

    def _handle_post_tool_use_failure(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self._buffer.tool_failures.append(
            {"tool": data.get("tool_name", ""), "error": data.get("error", "")}
        )
        self._emit_event("post_tool_use_failure", data)
        return {}

    def _handle_user_prompt_submit(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = data.get("prompt", data.get("user_message", ""))
        if isinstance(prompt, str):
            self._buffer.inputs.append(prompt)
        self._buffer.action_distribution["user_prompt"] += 1

        score = self._run_evaluation()
        self._emit_event("user_prompt_submit", data, score)
        return {}

    def _handle_stop(self, data: dict[str, Any]) -> dict[str, Any]:
        score = self._run_evaluation()
        output = (
            data.get("output")
            or data.get("response")
            or data.get("content")
            or ""
        )
        if not isinstance(output, str):
            output = str(output)
        self.record_decision(
            decision_point="stop",
            candidate_action={
                "type": "final_output",
                "content_hash": hashlib.sha256(output.encode()).hexdigest(),
                "summary": output[:120],
            },
            decision_inputs=self._collect_decision_inputs(),
        )
        self._emit_event("stop", data, score)
        return {}

    def _handle_subagent_start(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        agent_id = data.get("agent_id", "")
        self._buffer.subagents.append(
            {
                "agent_id": agent_id,
                "started": datetime.now(timezone.utc).isoformat(),
            }
        )
        if agent_id:
            self._buffer.subagent_starts_seen.add(agent_id)
        # subagent_start fires when the child starts running. The parent's
        # decision to delegate already happened earlier (often at the
        # parent's pre_tool_use if the subagent is invoked as a tool). So
        # this isn't strictly a "decision" — it's a lifecycle attestation
        # the chain records for completeness. The candidate_action.type
        # is honest about that so a downstream verifier can tell.
        self.record_decision(
            decision_point="subagent_start",
            candidate_action={
                "type": "subagent_dispatch_observed",
                "agent_id": agent_id,
                "boundary_category": "lifecycle_attestation",
            },
            decision_inputs=self._collect_decision_inputs(),
        )
        self._emit_event("subagent_start", data)
        return {}

    def _handle_subagent_stop(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        agent_id = data.get("agent_id", "")
        # Orphan handling: a _stop without a corresponding _start
        # (e.g., AutoGen OTel sampling drops the start span) would
        # silently corrupt the topology view. Emit a subagent_orphan
        # event so monitoring can see the gap.
        if agent_id and agent_id not in self._buffer.subagent_starts_seen:
            logger.warning(
                "%s saw subagent_stop for %r without matching start",
                self.name, agent_id,
            )
            self._emit_event(
                "subagent_orphan",
                {"agent_id": agent_id, "reason": "stop_without_start"},
            )
        self._buffer.subagents.append(
            {
                "agent_id": agent_id,
                "stopped": datetime.now(timezone.utc).isoformat(),
                "transcript_path": data.get("agent_transcript_path", ""),
            }
        )
        self._buffer.subagent_starts_seen.discard(agent_id)
        self._emit_event("subagent_stop", data)
        return {}

    def _handle_pre_compact(self, data: dict[str, Any]) -> dict[str, Any]:
        self._emit_event(
            "pre_compact",
            {
                "reasoning_chain_length": len(self._buffer.reasoning_chain),
                "archived_chain": list(self._buffer.reasoning_chain),
            },
        )
        return {}

    # --- Multi-agent handlers (v0.8) ---

    def _handle_topology_declared(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Fired once when the adapter receives its topology.

        The data dict carries a serialized ``AgentTopology``
        (``{"topology": topology.to_dict()}``). Triggers an
        attestation so the chain commits to the topology via
        ``Evidence(evidence_type="topology")``.
        """
        score = self._run_evaluation()
        self._emit_event("topology_declared", data, score)
        return {}

    def _handle_topology_change(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Fired when the topology mutates structurally.

        Triggers an attestation that carries both
        ``Evidence(evidence_type="topology", ...)`` (the new
        snapshot) and ``Evidence(evidence_type="topology_change", ...)``
        (the diff). ``_pending_topology_change`` was set by
        ``set_topology()`` and is consumed inside ``_run_evaluation``.
        """
        score = self._run_evaluation()
        self._emit_event("topology_change", data, score)
        return {}

    def _handle_peer_message(self, data: dict[str, Any]) -> dict[str, Any]:
        """A peer agent sent a message into this agent's context.

        Buffers the message under ``peer_messages`` so the
        AdversarialLayer can scan it. ``writer_agent_id`` is
        critical for downstream attack attribution.
        """
        entry = {
            "sender_agent_id": data.get("sender_agent_id", ""),
            "content": data.get("content", ""),
            "channel": data.get("channel", "peer_messages"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._buffer.peer_messages.append(entry)
        self._emit_event("peer_message", data)
        return {}

    def _handle_shared_memory_write(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        """A peer wrote data into shared memory that this agent reads.

        ``writer_agent_id`` is captured so shared-memory poisoning
        attributes the attack to the writer, not the reader (T-SHARED-
        MEM-MISATTRIB threat).
        """
        entry = {
            "writer_agent_id": data.get("writer_agent_id", ""),
            "key": data.get("key", ""),
            "content": data.get("content", ""),
            "content_hash": data.get("content_hash", ""),
            "summary": data.get("summary", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._buffer.shared_memory.append(entry)
        self._emit_event("shared_memory_write", data)
        return {}

    def _handle_broadcast(self, data: dict[str, Any]) -> dict[str, Any]:
        """A broadcast on a channel this agent subscribes to.

        Broadcasts can amplify (T-BROADCAST-AMP): N members × one
        broadcast = N evaluations. Adapters that fan a broadcast out
        to each member should rate-limit at the source. The buffer
        caps at 1000 entries per session as a defensive ceiling.
        """
        if len(self._buffer.broadcast_messages) >= 1000:
            self._emit_event(
                "broadcast_overflow",
                {"dropped": data, "limit": 1000},
            )
            return {}
        entry = {
            "sender_agent_id": data.get("sender_agent_id", ""),
            "channel": data.get("channel", "broadcast_channels"),
            "content": data.get("content", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._buffer.broadcast_messages.append(entry)
        self._emit_event("broadcast", data)
        return {}

    def _handle_task_started(self, data: dict[str, Any]) -> dict[str, Any]:
        """A task started — distinct from a subagent (e.g. CrewAI task
        vs CrewAI agent).

        Tasks ARE meaningful CrewAI primitives even if they aren't
        agents. Adapters can emit ``task_started`` alongside
        ``subagent_start`` to preserve task structure without
        conflating it with subagent counts.
        """
        entry = {
            "task_id": data.get("task_id", ""),
            "description": data.get("description", ""),
            "agent_id": data.get("agent_id", ""),
            "started": datetime.now(timezone.utc).isoformat(),
        }
        self._buffer.tasks.append(entry)
        self._emit_event("task_started", data)
        return {}

    def get_summary(self) -> dict[str, Any]:
        records = self._chain.records
        attestation_count = sum(
            1 for r in records if r.record_kind == "attestation"
        )
        decision_count = sum(
            1 for r in records if r.record_kind == "decision"
        )
        return {
            "adapter": self.name,
            "agent_id": self._profile.agent_id,
            "evaluations": self._evaluation_count,
            "events": len(self._events),
            "attestation_records": attestation_count,
            "decision_records": decision_count,
            "chain_records": len(records),
            "chain_valid": self._chain.verify_chain(),
            "enforce_mode": self._enforce,
        }
