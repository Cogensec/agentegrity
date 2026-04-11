"""
Integrity Monitor - wraps agent execution with continuous integrity
evaluation and configurable violation responses.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from agentegrity.core.attestation import AttestationChain, AttestationRecord, Evidence
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore
from agentegrity.core.profile import AgentProfile

logger = logging.getLogger("agentegrity.monitor")


class ViolationAction(str, Enum):
    """What to do when an integrity violation is detected."""

    LOG = "log"
    ALERT = "alert"
    BLOCK = "block"
    ESCALATE = "escalate"


@dataclass
class ViolationEvent:
    """Record of an integrity violation."""

    agent_id: str
    timestamp: datetime
    score: IntegrityScore
    action_taken: ViolationAction
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "timestamp": self.timestamp.isoformat(),
            "composite_score": self.score.composite,
            "action_taken": self.action_taken.value,
            "recommended_action": self.score.action,
            "context": self.context,
        }


class IntegrityMonitor:
    """
    Runtime integrity monitor that wraps agent execution with
    continuous evaluation.

    Parameters
    ----------
    profile : AgentProfile
        The agent being monitored.
    evaluator : IntegrityEvaluator
        The evaluator to use for integrity checks.
    threshold : float
        Minimum composite score. Below this triggers a violation.
    on_violation : ViolationAction
        Default action when a violation is detected.
    on_violation_callback : callable, optional
        Custom callback invoked on violation. Receives (ViolationEvent).
    enable_attestation : bool
        Whether to generate attestation records for each evaluation.

    Notes
    -----
    The monitor operates in fail-closed mode by default: when the
    evaluator's ``fail_fast`` flag is True and a layer returns a
    'block' action, the ``@guard`` decorator prevents the wrapped
    function from executing. This is appropriate when the library's
    checks are deterministic and pattern-based. v0.2 will add a
    fail_open mode for use with LLM-backed checks where
    infrastructure failures should not cascade into agent failures.

    Examples
    --------
    >>> monitor = IntegrityMonitor(profile, evaluator, threshold=0.75)
    >>> @monitor.guard
    ... async def agent_action(context):
    ...     return await agent.execute(context)
    """

    def __init__(
        self,
        profile: AgentProfile,
        evaluator: IntegrityEvaluator,
        threshold: float = 0.70,
        on_violation: ViolationAction = ViolationAction.ALERT,
        on_violation_callback: Callable[[ViolationEvent], None] | None = None,
        enable_attestation: bool = True,
    ):
        self.profile = profile
        self.evaluator = evaluator
        self.threshold = threshold
        self.on_violation = on_violation
        self.on_violation_callback = on_violation_callback
        self.enable_attestation = enable_attestation

        self._chain = AttestationChain()
        self._violations: list[ViolationEvent] = []
        self._evaluation_count: int = 0

    def evaluate(self, context: dict[str, Any] | None = None) -> IntegrityScore:
        """
        Run an integrity evaluation and handle the result.

        Returns the IntegrityScore. Triggers violation handling
        if the score falls below threshold.
        """
        score = self.evaluator.evaluate(self.profile, context)
        self._evaluation_count += 1

        # Generate attestation record
        if self.enable_attestation:
            record = AttestationRecord(
                agent_id=self.profile.agent_id,
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

        # Check for violations
        if score.composite < self.threshold or not score.passed:
            self._handle_violation(score, context)

        return score

    def guard(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator that wraps an agent function with integrity evaluation.

        Runs integrity checks before and after execution. If the pre-check
        fails and action is BLOCK, the function is not executed.
        """
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                context = kwargs.get("context", {})

                # Pre-execution check
                pre_score = self.evaluate(context)
                if pre_score.action == "block":
                    raise IntegrityViolationError(
                        f"Agent {self.profile.agent_id} blocked: "
                        f"integrity score {pre_score.composite:.3f} "
                        f"below threshold {self.threshold}"
                    )

                # Execute
                result = await func(*args, **kwargs)

                # Post-execution check
                post_context = {**context, "post_execution": True}
                self.evaluate(post_context)

                return result

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                context = kwargs.get("context", {})

                pre_score = self.evaluate(context)
                if pre_score.action == "block":
                    raise IntegrityViolationError(
                        f"Agent {self.profile.agent_id} blocked: "
                        f"integrity score {pre_score.composite:.3f} "
                        f"below threshold {self.threshold}"
                    )

                result = func(*args, **kwargs)

                post_context = {**context, "post_execution": True}
                self.evaluate(post_context)

                return result

            return sync_wrapper

    def _handle_violation(
        self,
        score: IntegrityScore,
        context: dict[str, Any] | None,
    ) -> None:
        """Process an integrity violation."""
        event = ViolationEvent(
            agent_id=self.profile.agent_id,
            timestamp=datetime.now(timezone.utc),
            score=score,
            action_taken=self.on_violation,
            context=context or {},
        )
        self._violations.append(event)

        # Log
        logger.warning(
            "Integrity violation for agent %s: score=%.3f, action=%s",
            self.profile.agent_id,
            score.composite,
            self.on_violation.value,
        )

        # Custom callback
        if self.on_violation_callback:
            self.on_violation_callback(event)

    @property
    def attestation_chain(self) -> AttestationChain:
        """Access the attestation chain for this monitor."""
        return self._chain

    @property
    def violations(self) -> list[ViolationEvent]:
        """Access the violation history."""
        return list(self._violations)

    @property
    def evaluation_count(self) -> int:
        return self._evaluation_count

    def __repr__(self) -> str:
        return (
            f"IntegrityMonitor(agent={self.profile.agent_id[:8]}..., "
            f"threshold={self.threshold}, "
            f"evaluations={self._evaluation_count}, "
            f"violations={len(self._violations)})"
        )


class IntegrityViolationError(Exception):
    """Raised when an agent action is blocked due to integrity violation."""

    pass
