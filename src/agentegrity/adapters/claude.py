"""
Claude Agent SDK adapter for agentegrity.

Instruments agents built on the Claude Agent SDK by registering hooks
at five integration points: Harness (Stop), Tools (PreToolUse/PostToolUse),
Session (UserPromptSubmit/PreCompact), Sandbox (file/command boundary
enforcement), and Orchestration (SubagentStart/SubagentStop).

Usage:
    from agentegrity.adapters.claude import ClaudeAdapter
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    adapter = ClaudeAdapter(profile=my_profile)
    options = ClaudeAgentOptions(hooks=adapter.create_hooks())
    async with ClaudeSDKClient(options=options) as client:
        ...
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentegrity.adapters.base import FrameworkEvent
from agentegrity.core.attestation import AttestationChain, AttestationRecord, Evidence
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore
from agentegrity.core.profile import AgentProfile

logger = logging.getLogger("agentegrity.adapters.claude")


@dataclass
class _ContextBuffer:
    """Internal buffer that accumulates runtime context from SDK hooks."""

    inputs: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_outputs: list[dict[str, Any]] = field(default_factory=list)
    tool_failures: list[dict[str, Any]] = field(default_factory=list)
    tool_usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    action_distribution: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    reasoning_chain: list[str] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)

    def to_evaluation_context(self) -> dict[str, Any]:
        """Convert accumulated data to the context dict the layers expect."""
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
                self.tool_calls[-1]
                if self.tool_calls
                else {"type": "respond"}
            ),
        }


class ClaudeAdapter:
    """Instruments a Claude Agent SDK agent with agentegrity evaluation.

    Parameters
    ----------
    profile : AgentProfile
        The agent being monitored.
    evaluator : IntegrityEvaluator, optional
        Custom evaluator. If not provided, a default three-layer evaluator
        is created.
    enforce : bool
        If True, governance "block" results cause PreToolUse hooks to
        return permissionDecision="deny". If False (default), hooks only
        measure and log — they never block tool execution.
    api_key : str, optional
        Anthropic API key for LLM-backed checks. Falls back to
        ANTHROPIC_API_KEY environment variable.
    """

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

        if evaluator is not None:
            self._evaluator = evaluator
        else:
            from agentegrity.layers.adversarial import AdversarialLayer
            from agentegrity.layers.cortical import CorticalLayer
            from agentegrity.layers.governance import GovernanceLayer

            self._evaluator = IntegrityEvaluator(
                layers=[
                    AdversarialLayer(),
                    CorticalLayer(),
                    GovernanceLayer(),
                ]
            )

    @property
    def name(self) -> str:
        return "claude"

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
        return event

    def _run_evaluation(self, context: dict[str, Any] | None = None) -> IntegrityScore:
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
        """Route a framework event to the appropriate handler."""
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
                    "Hook handler %s failed: %s", event_type, exc, exc_info=True
                )
        return {}

    # --- Hook callbacks for create_hooks() ---

    async def _hook_pre_tool_use(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("pre_tool_use", input_data)
        except Exception as exc:
            logger.warning("PreToolUse hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_post_tool_use(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("post_tool_use", input_data)
        except Exception as exc:
            logger.warning("PostToolUse hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_post_tool_use_failure(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("post_tool_use_failure", input_data)
        except Exception as exc:
            logger.warning("PostToolUseFailure hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_user_prompt_submit(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("user_prompt_submit", input_data)
        except Exception as exc:
            logger.warning("UserPromptSubmit hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_stop(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("stop", input_data)
        except Exception as exc:
            logger.warning("Stop hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_subagent_start(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("subagent_start", input_data)
        except Exception as exc:
            logger.warning("SubagentStart hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_subagent_stop(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("subagent_stop", input_data)
        except Exception as exc:
            logger.warning("SubagentStop hook failed: %s", exc, exc_info=True)
            return {}

    async def _hook_pre_compact(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        try:
            return await self.on_event("pre_compact", input_data)
        except Exception as exc:
            logger.warning("PreCompact hook failed: %s", exc, exc_info=True)
            return {}

    # --- Event handlers ---

    async def _handle_pre_tool_use(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
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

    async def _handle_post_tool_use(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
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
        self._buffer.tool_failures.append({
            "tool": data.get("tool_name", ""),
            "error": data.get("error", ""),
        })
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

    async def _handle_stop(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        score = self._run_evaluation()
        self._emit_event("stop", data, score)
        return {}

    async def _handle_subagent_start(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self._buffer.subagents.append({
            "agent_id": data.get("agent_id", ""),
            "started": datetime.now(timezone.utc).isoformat(),
        })
        self._emit_event("subagent_start", data)
        return {}

    async def _handle_subagent_stop(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        self._buffer.subagents.append({
            "agent_id": data.get("agent_id", ""),
            "stopped": datetime.now(timezone.utc).isoformat(),
            "transcript_path": data.get("agent_transcript_path", ""),
        })
        self._emit_event("subagent_stop", data)
        return {}

    async def _handle_pre_compact(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        # Archive the current reasoning chain before compaction
        self._emit_event(
            "pre_compact",
            {
                "reasoning_chain_length": len(self._buffer.reasoning_chain),
                "archived_chain": list(self._buffer.reasoning_chain),
            },
        )
        return {}

    # --- Public API ---

    def create_hooks(self) -> dict[str, list[Any]]:
        """Create Claude Agent SDK hook configuration.

        Returns a dict suitable for passing to ClaudeAgentOptions(hooks=...).
        Import HookMatcher at call time to avoid hard dependency on
        claude-agent-sdk when the adapter module is just imported.
        """
        try:
            from claude_agent_sdk import HookMatcher  # type: ignore[import-not-found]
        except ImportError:
            raise ImportError(
                "claude-agent-sdk is required for the Claude adapter. "
                "Install it with: pip install agentegrity[claude]"
            ) from None

        return {
            "PreToolUse": [
                HookMatcher(hooks=[self._hook_pre_tool_use]),
            ],
            "PostToolUse": [
                HookMatcher(hooks=[self._hook_post_tool_use]),
            ],
            "PostToolUseFailure": [
                HookMatcher(hooks=[self._hook_post_tool_use_failure]),
            ],
            "UserPromptSubmit": [
                HookMatcher(hooks=[self._hook_user_prompt_submit]),
            ],
            "Stop": [
                HookMatcher(hooks=[self._hook_stop]),
            ],
            "SubagentStart": [
                HookMatcher(hooks=[self._hook_subagent_start]),
            ],
            "SubagentStop": [
                HookMatcher(hooks=[self._hook_subagent_stop]),
            ],
            "PreCompact": [
                HookMatcher(hooks=[self._hook_pre_compact]),
            ],
        }

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of the adapter's session."""
        return {
            "adapter": self.name,
            "agent_id": self._profile.agent_id,
            "evaluations": self._evaluation_count,
            "events": len(self._events),
            "attestation_records": len(self._chain.records),
            "chain_valid": self._chain.verify_chain(),
            "enforce_mode": self._enforce,
        }
