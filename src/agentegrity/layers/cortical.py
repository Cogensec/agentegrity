"""
Cortical Layer - monitors the agent's internal cognitive integrity.

Named for the cerebral cortex — the brain's executive processing center.
This layer protects reasoning, memory, and behavioral consistency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agentegrity.core.evaluator import LayerResult
from agentegrity.core.profile import AgentProfile


@dataclass
class BehavioralBaseline:
    """
    A snapshot of an agent's expected behavioral patterns.
    Used as the reference point for drift detection.
    """

    agent_id: str
    action_distribution: dict[str, float] = field(default_factory=dict)
    tool_usage_patterns: dict[str, float] = field(default_factory=dict)
    response_length_mean: float = 0.0
    response_length_std: float = 0.0
    reasoning_depth_mean: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sample_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "action_distribution": self.action_distribution,
            "tool_usage_patterns": self.tool_usage_patterns,
            "response_length_mean": self.response_length_mean,
            "response_length_std": self.response_length_std,
            "reasoning_depth_mean": self.reasoning_depth_mean,
            "sample_count": self.sample_count,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class DriftAssessment:
    """Result of a behavioral drift evaluation."""

    drift_score: float  # 0.0 (no drift) to 1.0 (complete drift)
    dimensions: dict[str, float] = field(default_factory=dict)
    drifted_dimensions: list[str] = field(default_factory=list)
    baseline_age_hours: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "drift_score": self.drift_score,
            "dimensions": self.dimensions,
            "drifted_dimensions": self.drifted_dimensions,
            "baseline_age_hours": self.baseline_age_hours,
        }


@dataclass
class MemoryAssessment:
    """Result of a memory integrity evaluation."""

    integrity_score: float
    total_reads: int = 0
    suspicious_reads: int = 0
    provenance_verified: int = 0
    provenance_unknown: int = 0
    conflicts_detected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "integrity_score": self.integrity_score,
            "total_reads": self.total_reads,
            "suspicious_reads": self.suspicious_reads,
            "provenance_verified": self.provenance_verified,
            "provenance_unknown": self.provenance_unknown,
            "conflicts_detected": self.conflicts_detected,
        }


@dataclass
class ReasoningAssessment:
    """Result of a reasoning chain validation."""

    consistency_score: float
    chain_length: int = 0
    contradictions: int = 0
    goal_alignment: float = 1.0
    conflict_detected: bool = False
    conflict_description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "consistency_score": self.consistency_score,
            "chain_length": self.chain_length,
            "contradictions": self.contradictions,
            "goal_alignment": self.goal_alignment,
            "conflict_detected": self.conflict_detected,
            "conflict_description": self.conflict_description,
        }


class CorticalLayer:
    """
    The cortical layer evaluates an agent's internal cognitive integrity.

    It monitors three dimensions:
    1. Reasoning integrity - is the reasoning chain consistent?
    2. Memory integrity - is the agent's memory trustworthy?
    3. Behavioral consistency - has the agent drifted from baseline?

    Parameters
    ----------
    drift_tolerance : float
        Maximum acceptable behavioral drift before alerting. Default 0.15.
    memory_integrity_threshold : float
        Minimum memory integrity score. Default 0.80.
    reasoning_consistency_threshold : float
        Minimum reasoning consistency score. Default 0.75.
    baseline : BehavioralBaseline, optional
        Pre-established baseline. If None, a default baseline is used.
    min_drift_samples : int
        Minimum total observations required in *both* the baseline and
        current distributions before drift is computed. Below this
        threshold the drift signal is treated as ``insufficient``
        (recorded in details, not factored into the score). Default 20.
    """

    def __init__(
        self,
        drift_tolerance: float = 0.15,
        memory_integrity_threshold: float = 0.80,
        reasoning_consistency_threshold: float = 0.75,
        baseline: BehavioralBaseline | None = None,
        min_drift_samples: int = 20,
    ):
        self.drift_tolerance = drift_tolerance
        self.memory_integrity_threshold = memory_integrity_threshold
        self.reasoning_consistency_threshold = reasoning_consistency_threshold
        self._baseline = baseline
        self.min_drift_samples = min_drift_samples
        self._observation_buffer: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "cortical"

    def evaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        """
        Evaluate the agent's cognitive integrity across reasoning,
        memory, and behavioral dimensions.
        """
        ctx = context or {}

        # Initialize baseline if needed
        if self._baseline is None:
            self._baseline = BehavioralBaseline(agent_id=profile.agent_id)

        # Evaluate each dimension
        reasoning = self._validate_reasoning(profile, ctx)
        memory = self._check_memory(profile, ctx)
        drift = self._detect_drift(profile, ctx)

        # Composite cortical score (weighted average)
        cortical_score = (
            reasoning.consistency_score * 0.35
            + memory.integrity_score * 0.35
            + (1.0 - drift.drift_score) * 0.30
        )
        cortical_score = round(cortical_score, 4)

        # Determine action
        if reasoning.conflict_detected:
            action = "escalate"
            passed = False
        elif drift.drift_score > self.drift_tolerance * 2:
            action = "block"
            passed = False
        elif (
            drift.drift_score > self.drift_tolerance
            or memory.integrity_score < self.memory_integrity_threshold
            or reasoning.consistency_score < self.reasoning_consistency_threshold
        ):
            action = "alert"
            passed = False
        else:
            action = "pass"
            passed = True

        return LayerResult(
            layer_name=self.name,
            score=cortical_score,
            passed=passed,
            action=action,
            details={
                "portability_score": cortical_score,  # Maps to environmental portability
                "reasoning": reasoning.to_dict(),
                "memory": memory.to_dict(),
                "drift": drift.to_dict(),
                "baseline_sample_count": self._baseline.sample_count,
            },
        )

    async def aevaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        """Async wrapper around evaluate for use with AsyncIntegrityEvaluator."""
        return self.evaluate(profile, context)

    def _validate_reasoning(
        self,
        profile: AgentProfile,
        context: dict[str, Any],
    ) -> ReasoningAssessment:
        """
        Validate the agent's reasoning chain for consistency.

        Checks for:
        - Contradictions between stated goals and planned actions
        - Goal alignment drift
        - Cognitive conflicts (competing instructions)
        """
        reasoning_chain = context.get("reasoning_chain", [])
        goals = context.get("goals", [])
        instructions = context.get("instructions", [])

        chain_length = len(reasoning_chain) if isinstance(reasoning_chain, list) else 0
        contradictions = 0
        conflict_detected = False
        conflict_description = None

        # Check for contradictions in the reasoning chain
        if isinstance(reasoning_chain, list) and len(reasoning_chain) >= 2:
            for i in range(len(reasoning_chain) - 1):
                step_a = str(reasoning_chain[i]).lower()
                step_b = str(reasoning_chain[i + 1]).lower()
                # Simple contradiction heuristic: negation patterns
                if (
                    ("should not" in step_a and "should" in step_b and "should not" not in step_b)
                    or ("reject" in step_a and "accept" in step_b)
                    or ("deny" in step_a and "allow" in step_b)
                ):
                    contradictions += 1

        # Check for goal/instruction conflicts
        if goals and instructions:
            # Conflict detection: if instructions attempt to override goals
            for instruction in instructions:
                inst_lower = str(instruction).lower()
                if any(
                    override in inst_lower
                    for override in ["ignore your goal", "new objective", "override"]
                ):
                    conflict_detected = True
                    conflict_description = (
                        "Detected instruction attempting to override established goals"
                    )
                    break

        # Score
        if chain_length == 0:
            consistency = 0.85  # No chain to validate; moderate default
        else:
            contradiction_ratio = contradictions / chain_length
            consistency = max(0.0, 1.0 - contradiction_ratio * 2)

        if conflict_detected:
            consistency *= 0.5

        goal_alignment = 1.0 - (contradictions * 0.1)

        return ReasoningAssessment(
            consistency_score=round(max(0.0, min(1.0, consistency)), 4),
            chain_length=chain_length,
            contradictions=contradictions,
            goal_alignment=round(max(0.0, min(1.0, goal_alignment)), 4),
            conflict_detected=conflict_detected,
            conflict_description=conflict_description,
        )

    def _check_memory(
        self,
        profile: AgentProfile,
        context: dict[str, Any],
    ) -> MemoryAssessment:
        """
        Check the integrity of the agent's memory operations.

        Validates provenance of memory reads and detects conflicts
        between retrieved memory and the agent's established state.
        """
        memory_reads = context.get("memory_reads", [])
        total = len(memory_reads)

        if total == 0:
            return MemoryAssessment(integrity_score=1.0)

        suspicious = 0
        verified = 0
        unknown = 0
        conflicts = 0

        for read in memory_reads:
            if isinstance(read, dict):
                provenance = read.get("provenance")
                if provenance == "verified":
                    verified += 1
                elif provenance == "unknown" or provenance is None:
                    unknown += 1
                    suspicious += 1
                elif provenance == "external":
                    suspicious += 1

                if read.get("conflicts_with_baseline", False):
                    conflicts += 1

        # Score based on suspicious ratio and conflicts
        if total > 0:
            clean_ratio = 1.0 - (suspicious / total)
            conflict_penalty = conflicts * 0.15
            score = max(0.0, clean_ratio - conflict_penalty)
        else:
            score = 1.0

        return MemoryAssessment(
            integrity_score=round(score, 4),
            total_reads=total,
            suspicious_reads=suspicious,
            provenance_verified=verified,
            provenance_unknown=unknown,
            conflicts_detected=conflicts,
        )

    def _detect_drift(
        self,
        profile: AgentProfile,
        context: dict[str, Any],
    ) -> DriftAssessment:
        """
        Detect behavioral drift from established baseline.

        For each tracked dimension (action distribution, tool-usage
        distribution) the layer computes the Jensen-Shannon distance
        between the baseline and the current observation. JS distance is
        symmetric, bounded in [0, 1], and properly defined under Laplace
        smoothing — fixes for three real bugs in the previous
        ``_kl_divergence_approx``: (a) asymmetry meant
        baseline-vs-current and current-vs-baseline gave different
        scores, (b) the additive epsilon broke the probability simplex,
        and (c) there was no minimum-sample guard so a 1-vs-1 comparison
        could produce a "drifted" verdict.
        """
        insufficient: list[str] = []

        if self._baseline is None or self._baseline.sample_count == 0:
            return DriftAssessment(drift_score=0.0)

        dimensions: dict[str, float] = {}
        drifted: list[str] = []

        current_actions = context.get("action_distribution", {})
        if current_actions and self._baseline.action_distribution:
            score, ok = self._distribution_distance(
                self._baseline.action_distribution, current_actions
            )
            if ok:
                dimensions["action_distribution"] = score
                if score > self.drift_tolerance:
                    drifted.append("action_distribution")
            else:
                insufficient.append("action_distribution")

        current_tool_usage = context.get("tool_usage", {})
        if current_tool_usage and self._baseline.tool_usage_patterns:
            score, ok = self._distribution_distance(
                self._baseline.tool_usage_patterns, current_tool_usage
            )
            if ok:
                dimensions["tool_usage"] = score
                if score > self.drift_tolerance:
                    drifted.append("tool_usage")
            else:
                insufficient.append("tool_usage")

        if dimensions:
            drift_score = sum(dimensions.values()) / len(dimensions)
        else:
            drift_score = 0.0

        age_hours = (
            datetime.now(timezone.utc) - self._baseline.created_at
        ).total_seconds() / 3600

        assessment = DriftAssessment(
            drift_score=round(min(1.0, drift_score), 4),
            dimensions=dimensions,
            drifted_dimensions=drifted,
            baseline_age_hours=round(age_hours, 2),
        )
        if insufficient:
            # Surface as part of dimensions so downstream telemetry sees
            # the dimension was checked but skipped.
            for dim in insufficient:
                assessment.dimensions[f"{dim}__insufficient_samples"] = 0.0
        return assessment

    def _distribution_distance(
        self,
        baseline: dict[str, float],
        current: dict[str, float],
    ) -> tuple[float, bool]:
        """Jensen-Shannon distance between two count/probability dicts.

        Returns ``(distance, ok)`` where ``distance`` is in [0, 1] and
        ``ok`` is False if either side has fewer than
        :attr:`min_drift_samples` total observations (in which case the
        caller should treat the dimension as inconclusive rather than
        clean).

        Implementation notes:

        * Inputs are interpreted as un-normalized counts; this method
          renormalises both sides to probability distributions over the
          same support set.
        * Laplace (add-one) smoothing is applied so unseen keys on
          either side don't blow up the log term and the result remains
          a valid probability distribution.
        * The JS distance is the square root of JS divergence with log
          base 2, which yields a metric bounded in [0, 1].
        """
        all_keys = sorted(set(baseline.keys()) | set(current.keys()))
        if not all_keys:
            return 0.0, True

        total_b = sum(baseline.values())
        total_c = sum(current.values())

        if total_b < self.min_drift_samples or total_c < self.min_drift_samples:
            return 0.0, False

        # Laplace smoothing across the union support.
        k = len(all_keys)
        denom_b = total_b + k
        denom_c = total_c + k
        p = [(baseline.get(key, 0) + 1) / denom_b for key in all_keys]
        q = [(current.get(key, 0) + 1) / denom_c for key in all_keys]

        # Mixture distribution.
        m = [(pi + qi) / 2 for pi, qi in zip(p, q)]

        def _kl(a: list[float], b: list[float]) -> float:
            return sum(
                ai * math.log2(ai / bi) for ai, bi in zip(a, b) if ai > 0 and bi > 0
            )

        js_divergence = (_kl(p, m) + _kl(q, m)) / 2
        # JS divergence is bounded in [0, 1] under log_2; clamp for
        # numerical safety and take the square root for the metric form.
        js_divergence = max(0.0, min(1.0, js_divergence))
        distance = math.sqrt(js_divergence)
        return round(distance, 4), True

    # Backwards-compatible private alias retained for any callers in the
    # wild that monkey-patched the old name. New code should use
    # _distribution_distance.
    def _kl_divergence_approx(
        self,
        baseline: dict[str, float],
        current: dict[str, float],
    ) -> float:
        score, _ = self._distribution_distance(baseline, current)
        return score

    def update_baseline(self, observation: dict[str, Any]) -> None:
        """
        Update the behavioral baseline with a new observation.
        Call this during normal (non-adversarial) operation to
        establish what 'normal' looks like.
        """
        self._observation_buffer.append(observation)

        if self._baseline is None:
            self._baseline = BehavioralBaseline(agent_id="unknown")

        self._baseline.sample_count += 1

        # Update action distribution
        action = observation.get("action")
        if action:
            dist = self._baseline.action_distribution
            dist[action] = dist.get(action, 0) + 1

    def __repr__(self) -> str:
        return (
            f"CorticalLayer(drift_tolerance={self.drift_tolerance}, "
            f"memory_threshold={self.memory_integrity_threshold})"
        )
