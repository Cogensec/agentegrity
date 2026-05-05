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
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from agentegrity.core.attestation import AttestationChain, AttestationRecord, Evidence
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
    """Internal buffer accumulating runtime context from framework hooks."""

    inputs: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    tool_failures: list[dict[str, Any]] = field(default_factory=list)
    tool_usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    action_distribution: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    reasoning_chain: list[str] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)

    def to_evaluation_context(self) -> dict[str, Any]:
        return {
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
        }


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
    ) -> None:
        self._profile = profile
        self._enforce = enforce
        self._api_key = api_key
        self._buffer = _ContextBuffer()
        self._events: list[FrameworkEvent] = []
        self._chain = AttestationChain()
        self._evaluation_count = 0
        self._session_id = uuid4().hex
        self._exporters: list[SessionExporter] = []
        self._session_started = False
        self._session_ended = False

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

        record = AttestationRecord(
            agent_id=self._profile.agent_id,
            integrity_score=score.to_dict(),
            layer_states={r.layer_name: r.to_dict() for r in score.layer_results},
            evidence=[
                Evidence(
                    evidence_type="layer_result",
                    source=r.layer_name,
                    content_hash=str(hash(str(r.to_dict()))),
                    summary=f"{r.layer_name}: {r.score:.3f} ({r.action})",
                )
                for r in score.layer_results
            ],
        )
        self._chain.append(record)
        return score

    async def on_event(
        self, event_type: str, event_data: dict[str, Any]
    ) -> dict[str, Any]:
        handlers = {
            "pre_tool_use": self._handle_pre_tool_use,
            "post_tool_use": self._handle_post_tool_use,
            "post_tool_use_failure": self._handle_post_tool_use_failure,
            "user_prompt_submit": self._handle_user_prompt_submit,
            "stop": self._handle_stop,
            "subagent_start": self._handle_subagent_start,
            "subagent_stop": self._handle_subagent_stop,
            "pre_compact": self._handle_pre_compact,
        }
        handler = handlers.get(event_type)
        if handler:
            try:
                return await handler(event_data)
            except Exception as exc:
                logger.warning(
                    "%s handler %s failed: %s",
                    self.name,
                    event_type,
                    exc,
                    exc_info=True,
                )
        return {}

    # --- Framework-agnostic event handlers ---

    async def _handle_pre_tool_use(self, data: dict[str, Any]) -> dict[str, Any]:
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        self._buffer.tool_calls.append(
            {"tool": tool_name, "type": "tool_call", **tool_input}
        )
        self._buffer.tool_usage[tool_name] += 1
        self._buffer.action_distribution["tool_call"] += 1

        score = self._run_evaluation()
        self._emit_event("pre_tool_use", data, score)

        if self._enforce and score.action == "block":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Agentegrity integrity score {score.composite:.3f} "
                        f"triggered block action"
                    ),
                }
            }
        return {}

    async def _handle_post_tool_use(self, data: dict[str, Any]) -> dict[str, Any]:
        tool_response = data.get("tool_response", "")
        self._buffer.tool_outputs.append(
            {"tool": data.get("tool_name", ""), "output": tool_response}
        )
        score = self._run_evaluation()
        self._emit_event("post_tool_use", data, score)
        return {}

    async def _handle_post_tool_use_failure(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self._buffer.tool_failures.append(
            {"tool": data.get("tool_name", ""), "error": data.get("error", "")}
        )
        self._emit_event("post_tool_use_failure", data)
        return {}

    async def _handle_user_prompt_submit(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = data.get("prompt", data.get("user_message", ""))
        if isinstance(prompt, str):
            self._buffer.inputs.append(prompt)
        self._buffer.action_distribution["user_prompt"] += 1

        score = self._run_evaluation()
        self._emit_event("user_prompt_submit", data, score)
        return {}

    async def _handle_stop(self, data: dict[str, Any]) -> dict[str, Any]:
        score = self._run_evaluation()
        self._emit_event("stop", data, score)
        return {}

    async def _handle_subagent_start(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self._buffer.subagents.append(
            {
                "agent_id": data.get("agent_id", ""),
                "started": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._emit_event("subagent_start", data)
        return {}

    async def _handle_subagent_stop(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self._buffer.subagents.append(
            {
                "agent_id": data.get("agent_id", ""),
                "stopped": datetime.now(timezone.utc).isoformat(),
                "transcript_path": data.get("agent_transcript_path", ""),
            }
        )
        self._emit_event("subagent_stop", data)
        return {}

    async def _handle_pre_compact(self, data: dict[str, Any]) -> dict[str, Any]:
        self._emit_event(
            "pre_compact",
            {
                "reasoning_chain_length": len(self._buffer.reasoning_chain),
                "archived_chain": list(self._buffer.reasoning_chain),
            },
        )
        return {}

    def get_summary(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "agent_id": self._profile.agent_id,
            "evaluations": self._evaluation_count,
            "events": len(self._events),
            "attestation_records": len(self._chain.records),
            "chain_valid": self._chain.verify_chain(),
            "enforce_mode": self._enforce,
        }
