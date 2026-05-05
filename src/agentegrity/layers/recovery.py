"""
Recovery Layer - verifies the agent's self-recovery capability.

The recovery layer assesses whether an agent can detect when its
integrity has been compromised and restore itself to a known-good
state. This is the third self-securing capability after self-defense
(adversarial layer) and self-stability (cortical layer).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentegrity.core.attestation import AttestationChain
from agentegrity.core.evaluator import LayerResult
from agentegrity.core.profile import AgentProfile
from agentegrity.layers.checkpoint import (
    Checkpoint,
    CheckpointSnapshot,
)

logger = logging.getLogger("agentegrity.recovery")

RECOVERY_CAPABILITIES = {"state_restore", "checkpoint", "rollback", "session_reset"}


@dataclass
class RecoveryAssessment:
    """Result of a recovery integrity evaluation."""

    recovery_score: float
    has_baseline: bool = False
    baseline_age_hours: float = 0.0
    chain_intact: bool = True
    chain_length: int = 0
    sustained_degradation: bool = False
    degradation_trend: list[float] = field(default_factory=list)
    recovery_capable: bool = False
    recovery_capabilities_present: list[str] = field(default_factory=list)
    checkpoint_count: int = 0
    last_checkpoint_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_score": self.recovery_score,
            "has_baseline": self.has_baseline,
            "baseline_age_hours": self.baseline_age_hours,
            "chain_intact": self.chain_intact,
            "chain_length": self.chain_length,
            "sustained_degradation": self.sustained_degradation,
            "degradation_trend": self.degradation_trend,
            "recovery_capable": self.recovery_capable,
            "recovery_capabilities_present": self.recovery_capabilities_present,
            "checkpoint_count": self.checkpoint_count,
            "last_checkpoint_id": self.last_checkpoint_id,
        }


class RecoveryLayer:
    """
    Verifies the agent's self-recovery capability.

    Checks:
    1. State checkpoint verification — restorable baseline exists
    2. Compromise detection — sustained score degradation in history
    3. Recovery capability assessment — agent profile has recovery mechanisms
    4. Session continuity verification — attestation chain is intact

    Parameters
    ----------
    degradation_window : int
        Number of recent scores to examine for sustained degradation.
    degradation_threshold : float
        Score drop over the window that indicates persistent compromise.
    chain : AttestationChain, optional
        Attestation chain to verify for continuity.
    score_history : list[float], optional
        Recent composite scores for degradation analysis.
    checkpoint : Checkpoint, optional
        A persistence backend (in-memory / file / sqlite / custom) that
        :meth:`snapshot` writes to and :meth:`restore_to` reads from.
        When supplied, the layer reports ``checkpoint_count`` and
        ``last_checkpoint_id`` in its result details and treats
        attached-checkpoint as a recovery capability signal.
    """

    def __init__(
        self,
        degradation_window: int = 10,
        degradation_threshold: float = 0.15,
        chain: AttestationChain | None = None,
        score_history: list[float] | None = None,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        self._degradation_window = degradation_window
        self._degradation_threshold = degradation_threshold
        self._chain = chain
        self._score_history: list[float] = score_history or []
        self._checkpoint = checkpoint
        self._last_checkpoint_id: str | None = None

    @property
    def name(self) -> str:
        return "recovery"

    def record_score(self, score: float) -> None:
        """Append a composite score to the history for degradation tracking."""
        self._score_history.append(score)

    def evaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        ctx = context or {}

        assessment = self._assess_recovery(profile, ctx)

        passed = assessment.recovery_score >= 0.5
        if assessment.sustained_degradation:
            action = "alert"
        elif not assessment.chain_intact:
            action = "escalate"
        elif passed:
            action = "pass"
        else:
            action = "alert"

        return LayerResult(
            layer_name=self.name,
            score=assessment.recovery_score,
            passed=passed,
            action=action,
            details={
                "recovery_score": assessment.recovery_score,
                **assessment.to_dict(),
            },
        )

    async def aevaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        """Async wrapper around evaluate for use with AsyncIntegrityEvaluator."""
        return self.evaluate(profile, context)

    def _assess_recovery(
        self,
        profile: AgentProfile,
        context: dict[str, Any],
    ) -> RecoveryAssessment:
        scores: list[float] = []

        # 1. State checkpoint verification
        baseline_score, has_baseline, baseline_age = self._check_baseline(context)
        scores.append(baseline_score)

        # 2. Compromise detection (sustained degradation)
        degradation_score, sustained, trend = self._check_degradation()
        scores.append(degradation_score)

        # 3. Recovery capability assessment
        capability_score, capable, caps = self._check_capabilities(profile)
        scores.append(capability_score)

        # 4. Session continuity (attestation chain integrity)
        chain_score, chain_intact, chain_len = self._check_chain()
        scores.append(chain_score)

        composite = sum(scores) / len(scores) if scores else 0.0

        checkpoint_count = (
            len(self._checkpoint.list_ids()) if self._checkpoint is not None else 0
        )

        return RecoveryAssessment(
            recovery_score=round(composite, 4),
            has_baseline=has_baseline,
            baseline_age_hours=baseline_age,
            chain_intact=chain_intact,
            chain_length=chain_len,
            sustained_degradation=sustained,
            degradation_trend=trend,
            recovery_capable=capable,
            recovery_capabilities_present=caps,
            checkpoint_count=checkpoint_count,
            last_checkpoint_id=self._last_checkpoint_id,
        )

    def _check_baseline(
        self, context: dict[str, Any]
    ) -> tuple[float, bool, float]:
        """Check if a restorable baseline state exists and is recent."""
        baseline = context.get("behavioral_baseline")
        if baseline is None:
            return 0.5, False, 0.0

        # Check age
        created_at = baseline.get("created_at")
        age_hours = 0.0
        if created_at:
            try:
                if isinstance(created_at, str):
                    created = datetime.fromisoformat(created_at)
                elif isinstance(created_at, datetime):
                    created = created_at
                else:
                    return 0.6, True, 0.0
                now = datetime.now(timezone.utc)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (now - created).total_seconds() / 3600
            except (ValueError, TypeError):
                age_hours = 0.0

        sample_count = baseline.get("sample_count", 0)
        has_baseline = sample_count > 0

        if not has_baseline:
            return 0.5, False, age_hours

        # Fresh baseline with samples is ideal
        if age_hours < 24 and sample_count >= 5:
            score = 1.0
        elif age_hours < 72:
            score = 0.85
        elif age_hours < 168:  # one week
            score = 0.7
        else:
            score = 0.5  # stale baseline

        return score, has_baseline, round(age_hours, 2)

    def _check_degradation(self) -> tuple[float, bool, list[float]]:
        """Check for sustained score degradation in recent history."""
        history = self._score_history[-self._degradation_window:]
        if len(history) < 3:
            return 1.0, False, history

        first_half = history[: len(history) // 2]
        second_half = history[len(history) // 2 :]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)

        drop = avg_first - avg_second
        sustained = drop >= self._degradation_threshold

        if sustained:
            score = max(0.0, 1.0 - (drop * 3))
        elif drop > 0:
            score = max(0.5, 1.0 - drop)
        else:
            score = 1.0

        return round(score, 4), sustained, history

    def _check_capabilities(
        self, profile: AgentProfile
    ) -> tuple[float, bool, list[str]]:
        """Check if agent profile indicates recovery mechanisms.

        An attached :class:`Checkpoint` backend is treated as a synthetic
        ``checkpoint`` capability for scoring purposes — restorable
        baselines are the operational meaning of the declared capability,
        so wiring up a real backend should not be undercounted.
        """
        present = [
            cap for cap in profile.capabilities if cap in RECOVERY_CAPABILITIES
        ]
        if self._checkpoint is not None and "checkpoint" not in present:
            present = [*present, "checkpoint"]
        capable = len(present) > 0

        if len(present) >= 3:
            score = 1.0
        elif len(present) == 2:
            score = 0.85
        elif len(present) == 1:
            score = 0.7
        else:
            score = 0.4

        return score, capable, present

    def _check_chain(self) -> tuple[float, bool, int]:
        """Verify attestation chain integrity."""
        if self._chain is None:
            return 0.5, True, 0

        chain_len = len(self._chain.records)
        intact = self._chain.verify_chain()

        if not intact:
            return 0.0, False, chain_len

        if chain_len == 0:
            return 0.5, True, 0

        return 1.0, True, chain_len

    # ------------------------------------------------------------------
    # Checkpoint API: snapshot the layer's restorable state and roll back
    # to a previous snapshot.
    # ------------------------------------------------------------------

    def snapshot(
        self,
        agent_id: str,
        baseline: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Persist the layer's restorable state to the configured checkpoint.

        Captures the current attestation chain (full record dicts),
        score history, optional behavioural baseline, and arbitrary
        ``metadata``. Returns the canonical id assigned by the backend.

        Raises
        ------
        RuntimeError
            If no ``checkpoint=`` backend was supplied at construction.
        """
        if self._checkpoint is None:
            raise RuntimeError(
                "RecoveryLayer was constructed without a checkpoint backend; "
                "pass checkpoint=Checkpoint(...) to enable snapshot()/restore_to()."
            )
        snap = CheckpointSnapshot(
            agent_id=agent_id,
            score_history=list(self._score_history),
            chain_records=(
                self._chain.to_records_dict() if self._chain is not None else []
            ),
            baseline=baseline,
            metadata=metadata or {},
        )
        cid = self._checkpoint.save(snap)
        self._last_checkpoint_id = cid
        return cid

    def restore_to(self, checkpoint_id: str) -> CheckpointSnapshot:
        """Roll the layer back to the named checkpoint.

        Replaces ``_score_history`` with the snapshot's history and
        rebuilds the :class:`AttestationChain` from the snapshot's
        record dicts (preserving original link hashes so
        ``verify_chain()`` returns True post-restore).

        If the layer was constructed without a chain object the
        snapshot's chain records are loaded into a freshly-created chain
        on the layer.

        Raises
        ------
        RuntimeError
            If no checkpoint backend is configured.
        KeyError
            If the snapshot cannot be loaded.
        """
        if self._checkpoint is None:
            raise RuntimeError(
                "RecoveryLayer was constructed without a checkpoint backend; "
                "restore_to() is unavailable."
            )
        snap = self._checkpoint.load(checkpoint_id)
        if snap is None:
            raise KeyError(f"checkpoint not found: {checkpoint_id!r}")

        self._score_history = list(snap.score_history)
        if snap.chain_records:
            self._chain = AttestationChain.from_dict_list(snap.chain_records)
        elif self._chain is not None:
            # Snapshot had an empty chain; reflect that.
            self._chain = AttestationChain()
        self._last_checkpoint_id = snap.checkpoint_id
        return snap
