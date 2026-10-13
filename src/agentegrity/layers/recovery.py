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
from typing import Any, Callable

from agentegrity.core.attestation import AttestationChain, ChainedRecord
from agentegrity.core.decision import build_decision_record
from agentegrity.core.evaluator import LayerResult
from agentegrity.core.profile import AgentProfile
from agentegrity.layers.checkpoint import (
    Checkpoint,
    CheckpointSnapshot,
)

logger = logging.getLogger("agentegrity.recovery")

# Recovery mechanisms a profile can declare. Only those with an attached
# implementation count toward the score (see _check_capabilities).
RECOVERY_CAPABILITIES = {
    "state_restore",
    "checkpoint",
    "rollback",
    "session_reset",
    "peer_quarantine",  # v0.8: multi-agent, isolate a compromised peer.
}

# Snapshot metadata kind for a chain saved before a restore replaced it.
_ARCHIVE_KIND = "pre_restore_archive"


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
    recovery_capabilities_unbacked: list[str] = field(default_factory=list)
    checkpoint_count: int = 0
    last_checkpoint_id: str | None = None
    # v0.8 multi-agent extensions
    cascade_compromise_suspected: bool = False
    degrading_peer_ids: list[str] = field(default_factory=list)
    quarantine_capable: bool = False

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
            "recovery_capabilities_unbacked": self.recovery_capabilities_unbacked,
            "checkpoint_count": self.checkpoint_count,
            "last_checkpoint_id": self.last_checkpoint_id,
            "cascade_compromise_suspected": self.cascade_compromise_suspected,
            "degrading_peer_ids": self.degrading_peer_ids,
            "quarantine_capable": self.quarantine_capable,
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

    def bind_chain(self, chain: AttestationChain) -> None:
        """Check continuity of ``chain``, which an adapter or monitor writes, unless one is set."""
        if self._chain is None:
            self._chain = chain
        elif self._chain is not chain:
            logger.warning(
                "RecoveryLayer already verifies another attestation chain; "
                "share one evaluator per adapter so continuity covers this one"
            )

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
        elif assessment.cascade_compromise_suspected:
            # T-CASCADE: even if THIS agent's own metrics are fine,
            # correlated peer degradation is a signal something is
            # propagating. Escalate to alert so a human can decide
            # whether to quarantine.
            action = "alert"
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
        capability_score, capable, caps, unbacked = self._check_capabilities(profile)
        scores.append(capability_score)

        # 4. Session continuity (attestation chain integrity)
        chain_score, chain_intact, chain_len = self._check_chain()
        scores.append(chain_score)

        # 5. v0.8 multi-agent: cascade detection over peer score
        #    history. T-CASCADE — correlated degradation across two
        #    or more peers is signal that compromise is propagating.
        cascade_suspected, degrading_peers = self._check_cascade(context)

        quarantine_capable = "peer_quarantine" in caps

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
            recovery_capabilities_unbacked=unbacked,
            checkpoint_count=checkpoint_count,
            last_checkpoint_id=self._last_checkpoint_id,
            cascade_compromise_suspected=cascade_suspected,
            degrading_peer_ids=degrading_peers,
            quarantine_capable=quarantine_capable,
        )

    def _check_cascade(
        self, context: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        """Detect correlated degradation across peers (T-CASCADE).

        Reads ``peer_score_history`` from the topology context. A
        peer is "degrading" when its score trend over the
        degradation window drops by more than the configured
        threshold (matches the single-agent sustained-degradation
        check). Two or more degrading peers signals a potential
        cascade.
        """
        topo_ctx = context.get("topology_context") or {}
        peer_history = topo_ctx.get("peer_score_history") or {}
        if not isinstance(peer_history, dict):
            return False, []

        degrading: list[str] = []
        for peer_id, history in peer_history.items():
            if not isinstance(history, list) or len(history) < self._degradation_window:
                continue
            window = history[-self._degradation_window:]
            drop = max(window) - min(window)
            # The drop must end downward (recent < older). If the
            # window's first half median is higher than the second
            # half median by more than threshold, the trend is down.
            half = len(window) // 2
            if half == 0:
                continue
            first_half_max = max(window[:half])
            second_half_min = min(window[half:])
            if (
                drop >= self._degradation_threshold
                and second_half_min < first_half_max - self._degradation_threshold
            ):
                degrading.append(str(peer_id))

        cascade = len(degrading) >= 2
        return cascade, sorted(degrading)

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
    ) -> tuple[float, bool, list[str], list[str]]:
        """Score the recovery mechanisms that have an implementation attached.

        A capability counts only when something can perform it: an attached
        :class:`Checkpoint` backend provides ``checkpoint``. Recovery
        capabilities the profile declares without one are returned as
        unbacked and do not raise the score, so listing a capability in a
        profile cannot inflate it.
        """
        present = ["checkpoint"] if self._checkpoint is not None else []
        unbacked = sorted({
            cap for cap in profile.capabilities
            if cap in RECOVERY_CAPABILITIES and cap not in present
        })
        capable = len(present) > 0

        if len(present) >= 3:
            score = 1.0
        elif len(present) == 2:
            score = 0.85
        elif len(present) == 1:
            score = 0.7
        else:
            score = 0.4

        return score, capable, present, unbacked

    def _check_chain(self) -> tuple[float, bool, int]:
        """Verify attestation chain integrity; neutral when no chain is bound.

        A bound chain that is still empty is the start of a session, which
        is intact, so the first evaluation scores like the ones after it.
        """
        if self._chain is None:
            return 0.5, True, 0

        chain_len = len(self._chain.records)
        if not self._chain.verify_chain():
            return 0.0, False, chain_len
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
        backend = self._require_checkpoint()
        snap = CheckpointSnapshot(
            agent_id=agent_id,
            score_history=list(self._score_history),
            chain_records=(
                self._chain.to_records_dict() if self._chain is not None else []
            ),
            baseline=baseline,
            metadata=metadata or {},
        )
        cid = backend.save(snap)
        self._last_checkpoint_id = cid
        return cid

    def restore_to(self,
        checkpoint_id: str,
        *,
        signing_key: Any | None = None,
        trusted_keys: set[bytes] | None = None,
    ) -> CheckpointSnapshot:
        """Roll the score history back to a checkpoint without losing any attestation record.

        The chain is evidence, so it is never rolled back. An intact chain
        keeps every record and gains a ``recovery_restore`` decision record.
        A chain that no longer verifies is saved in full to the checkpoint
        backend as an archive, then continues from the checkpoint's records;
        the recovery record cites the archive. With no chain yet, the
        checkpoint's chain is adopted.

        ``signing_key`` signs the recovery record, and is required when the
        chain holds signed records, so restoring never leaves a signed chain
        failing ``verify_signatures``. Without ``trusted_keys`` the backend
        is trusted: anyone who can write to it can re-link a modified chain
        under the same record ids. With ``trusted_keys`` every checkpoint
        record must carry a valid signature from one of them. The score
        history in a checkpoint is not signed either way.

        Call it while the adapter is not handling events: the adapter's
        lock does not cover a restore.

        Raises
        ------
        RuntimeError
            If no checkpoint backend is configured.
        KeyError
            If the snapshot cannot be loaded.
        ValueError
            If the snapshot is an archive, its chain does not verify or is
            not signed by ``trusted_keys``, it is not from this chain's
            history, or the chain is signed and no ``signing_key`` is given.
        """
        backend = self._require_checkpoint()
        snap = backend.load(checkpoint_id)
        if snap is None:
            raise KeyError(f"checkpoint not found: {checkpoint_id!r}")
        if snap.metadata.get("kind") == _ARCHIVE_KIND:
            raise ValueError(f"checkpoint {checkpoint_id!r} is an archive, not a restore point")
        restored = AttestationChain.from_dict_list(snap.chain_records)
        if not restored.verify_chain():
            raise ValueError(f"checkpoint {checkpoint_id!r} holds a chain that does not verify")
        if trusted_keys is not None and not restored.verify_signatures(trusted_keys)[0]:
            raise ValueError(f"checkpoint {checkpoint_id!r} is not signed by a trusted key")
        live = self._chain.records if self._chain is not None else []
        if signing_key is None and any(r.signature is not None for r in [*live, *restored.records]):
            raise ValueError("the chain is signed; pass signing_key so the recovery record is too")

        archive_id: str | None = None
        archived = 0
        if self._chain is None:
            self._chain = restored
        elif self._chain.verify_chain():
            if not _is_prefix(restored, self._chain, key=lambda r: r.content_hash):
                raise ValueError(f"checkpoint {checkpoint_id!r} is not from this chain's history")
        else:
            if not _is_prefix(restored, self._chain, key=lambda r: r.record_id):
                raise ValueError(f"checkpoint {checkpoint_id!r} is not from this chain's history")
            archived = len(self._chain)
            archive_id = backend.save(CheckpointSnapshot(
                agent_id=snap.agent_id,
                score_history=list(self._score_history),
                chain_records=self._chain.to_records_dict(),
                metadata={"kind": _ARCHIVE_KIND, "restored_to": snap.checkpoint_id},
            ))
            self._chain.restart_from(restored.records)

        self._score_history = list(snap.score_history)
        latest = self._chain.latest
        self._chain.append(build_decision_record(
            snap.agent_id,
            "recovery_restore",
            {
                "type": "restore",
                "checkpoint_id": snap.checkpoint_id,
                "archive_id": archive_id,
                "archived_records": archived,
            },
            reasoning_chain=[f"chain_intact:{str(archive_id is None).lower()}"],
            previous_record_hash=latest.content_hash if latest is not None else None,
            signing_key=signing_key,
        ))
        self._last_checkpoint_id = snap.checkpoint_id
        return snap

    def _require_checkpoint(self) -> Checkpoint:
        if self._checkpoint is None:
            raise RuntimeError(
                "RecoveryLayer was constructed without a checkpoint backend; "
                "pass checkpoint=Checkpoint(...) to enable snapshot()/restore_to()."
            )
        return self._checkpoint


def _is_prefix(prefix: AttestationChain,
    chain: AttestationChain,
    *,
    key: Callable[[ChainedRecord], str],
) -> bool:
    """Whether ``prefix`` holds the same records, in order, as the start of ``chain``."""
    head = chain.records[: len(prefix)]
    return len(head) == len(prefix) and all(
        key(a) == key(b) for a, b in zip(prefix.records, head)
    )
