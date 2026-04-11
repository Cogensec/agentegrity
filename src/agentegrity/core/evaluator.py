"""
Integrity Evaluator - orchestrates the three-layer evaluation pipeline
and produces composite integrity scores.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


class Layer(Protocol):
    """Protocol for integrity layers. All three layers implement this."""

    @property
    def name(self) -> str: ...

    def evaluate(self, profile: Any, context: dict[str, Any] | None = None) -> LayerResult: ...


@dataclass
class LayerResult:
    """Result from a single layer evaluation."""

    layer_name: str
    score: float  # 0.0 - 1.0
    passed: bool
    action: str  # "pass" | "alert" | "block" | "escalate"
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_name": self.layer_name,
            "score": self.score,
            "passed": self.passed,
            "action": self.action,
            "details": self.details,
            "latency_ms": self.latency_ms,
        }


@dataclass
class PropertyScores:
    """Per-property breakdown of integrity evaluation."""

    adversarial_coherence: float = 0.0
    environmental_portability: float = 0.0
    verifiable_assurance: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "adversarial_coherence": self.adversarial_coherence,
            "environmental_portability": self.environmental_portability,
            "verifiable_assurance": self.verifiable_assurance,
        }


@dataclass
class IntegrityScore:
    """
    Composite integrity score with per-property and per-layer breakdowns.

    The composite score is a weighted aggregation of the three property scores.
    """

    composite: float
    properties: PropertyScores
    layer_results: list[LayerResult]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0
    evaluator_version: str = "1.0.0"
    total_latency_ms: float = 0.0

    @property
    def passed(self) -> bool:
        """Whether all layers passed."""
        return all(r.passed for r in self.layer_results)

    @property
    def action(self) -> str:
        """The most severe action recommended across all layers."""
        severity = {"pass": 0, "alert": 1, "escalate": 2, "block": 3}
        if not self.layer_results:
            return "pass"
        return max(self.layer_results, key=lambda r: severity.get(r.action, 0)).action

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite": self.composite,
            "properties": self.properties.to_dict(),
            "layer_results": [r.to_dict() for r in self.layer_results],
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "evaluator_version": self.evaluator_version,
            "total_latency_ms": self.total_latency_ms,
            "passed": self.passed,
            "action": self.action,
        }

    def __repr__(self) -> str:
        status = "PASS" if self.passed else f"FAIL ({self.action})"
        return f"IntegrityScore({self.composite:.3f}, {status})"


@dataclass
class PropertyWeights:
    """Configurable weights for compositing the three property scores."""

    adversarial_coherence: float = 0.40
    environmental_portability: float = 0.25
    verifiable_assurance: float = 0.35

    def __post_init__(self) -> None:
        total = (
            self.adversarial_coherence
            + self.environmental_portability
            + self.verifiable_assurance
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"Property weights must sum to 1.0, got {total:.3f}. "
                f"Current weights: AC={self.adversarial_coherence}, "
                f"EP={self.environmental_portability}, "
                f"VA={self.verifiable_assurance}"
            )


class IntegrityEvaluator:
    """
    Orchestrates the three-layer evaluation pipeline and produces
    composite integrity scores.

    Parameters
    ----------
    layers : list[Layer]
        The integrity layers to evaluate. Standard configuration uses
        AdversarialLayer, CorticalLayer, and GovernanceLayer.
    weights : PropertyWeights, optional
        Weights for compositing property scores. Defaults to
        AC=0.40, EP=0.25, VA=0.35.
    fail_fast : bool
        If True, stop evaluation on the first blocking result.
        Defaults to True.

        When True (default), evaluation stops at the first layer that
        returns a 'block' action and the agent action is prevented from
        executing. This is appropriate when the library's checks are
        deterministic and pattern-based. v0.2 will add a fail_open mode
        for use with LLM-backed checks where infrastructure failures
        should not cascade into agent failures.

    Examples
    --------
    >>> from agentegrity.layers import AdversarialLayer, CorticalLayer, GovernanceLayer
    >>> evaluator = IntegrityEvaluator(
    ...     layers=[
    ...         AdversarialLayer(coherence_threshold=0.85),
    ...         CorticalLayer(drift_tolerance=0.10),
    ...         GovernanceLayer(policy_set="enterprise-default"),
    ...     ]
    ... )
    >>> result = evaluator.evaluate(profile)
    """

    def __init__(
        self,
        layers: list[Layer],
        weights: PropertyWeights | None = None,
        fail_fast: bool = True,
    ):
        self.layers = layers
        self.weights = weights or PropertyWeights()
        self.fail_fast = fail_fast
        self._version = "1.0.0"

    def evaluate(
        self,
        profile: Any,
        context: dict[str, Any] | None = None,
    ) -> IntegrityScore:
        """
        Run the full evaluation pipeline across all layers.

        Parameters
        ----------
        profile : AgentProfile
            The agent to evaluate.
        context : dict, optional
            Runtime context (current action, environment state, etc.)

        Returns
        -------
        IntegrityScore
            Composite score with per-property and per-layer breakdowns.
        """
        start = time.perf_counter()
        layer_results: list[LayerResult] = []
        ctx = context or {}

        for layer in self.layers:
            layer_start = time.perf_counter()
            result = layer.evaluate(profile, ctx)
            result.latency_ms = (time.perf_counter() - layer_start) * 1000
            layer_results.append(result)

            if self.fail_fast and result.action == "block":
                break

        # Compute property scores from layer results
        properties = self._compute_property_scores(layer_results)

        # Composite score
        composite = (
            self.weights.adversarial_coherence * properties.adversarial_coherence
            + self.weights.environmental_portability * properties.environmental_portability
            + self.weights.verifiable_assurance * properties.verifiable_assurance
        )

        total_latency = (time.perf_counter() - start) * 1000

        return IntegrityScore(
            composite=round(composite, 4),
            properties=properties,
            layer_results=layer_results,
            confidence=self._compute_confidence(layer_results),
            evaluator_version=self._version,
            total_latency_ms=round(total_latency, 2),
        )

    def _compute_property_scores(self, results: list[LayerResult]) -> PropertyScores:
        """
        Map layer results to the three agentegrity properties.

        The mapping is:
        - Adversarial coherence: primarily from adversarial layer
        - Environmental portability: derived from cross-layer consistency
        - Verifiable assurance: derived from attestation completeness
        """
        scores = PropertyScores()

        for r in results:
            details = r.details

            # Adversarial coherence: pull from adversarial layer or coherence key
            if "coherence_score" in details:
                scores.adversarial_coherence = max(
                    scores.adversarial_coherence, details["coherence_score"]
                )

            # Environmental portability: pull from portability key
            if "portability_score" in details:
                scores.environmental_portability = max(
                    scores.environmental_portability, details["portability_score"]
                )

            # Verifiable assurance: pull from assurance key
            if "assurance_score" in details:
                scores.verifiable_assurance = max(
                    scores.verifiable_assurance, details["assurance_score"]
                )

        # Fallback: if no explicit property scores, derive from layer scores
        if scores.adversarial_coherence == 0.0 and results:
            adversarial_results = [r for r in results if "adversarial" in r.layer_name.lower()]
            if adversarial_results:
                scores.adversarial_coherence = adversarial_results[0].score

        if scores.environmental_portability == 0.0 and results:
            # Portability is the minimum score across layers (weakest link)
            scores.environmental_portability = min(r.score for r in results)

        if scores.verifiable_assurance == 0.0 and results:
            # Assurance defaults to average completeness
            scores.verifiable_assurance = sum(r.score for r in results) / len(results)

        return scores

    def _compute_confidence(self, results: list[LayerResult]) -> float:
        """
        Confidence in the evaluation. Reduced when layers are missing
        or evaluation is incomplete.
        """
        if not results:
            return 0.0

        expected_layers = len(self.layers)
        actual_layers = len(results)
        coverage = actual_layers / expected_layers if expected_layers > 0 else 0.0

        # Confidence is also reduced if any layer has low detail
        detail_completeness = sum(
            1.0 if r.details else 0.5 for r in results
        ) / actual_layers

        return round(min(coverage, detail_completeness), 4)

    def __repr__(self) -> str:
        layer_names = [layer.name for layer in self.layers]
        return f"IntegrityEvaluator(layers={layer_names})"
