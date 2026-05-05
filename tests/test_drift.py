"""Tests for the upgraded behavioural drift detector in CorticalLayer.

The previous implementation used an asymmetric forward KL with additive
epsilon and no minimum-sample guard. The new implementation computes
Jensen-Shannon distance with Laplace smoothing and treats below-threshold
sample sizes as ``insufficient`` rather than ``no drift``.
"""

from __future__ import annotations

import math

from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers.cortical import BehavioralBaseline, CorticalLayer


def _profile() -> AgentProfile:
    return AgentProfile(
        name="t",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _layer_with_baseline(action_dist: dict[str, float]) -> CorticalLayer:
    layer = CorticalLayer(drift_tolerance=0.15, min_drift_samples=20)
    layer._baseline = BehavioralBaseline(
        agent_id="t",
        action_distribution=dict(action_dist),
        sample_count=int(sum(action_dist.values())),
    )
    return layer


class TestDistributionDistance:
    def test_identical_distributions_return_zero(self):
        layer = CorticalLayer(min_drift_samples=10)
        d = {"a": 50, "b": 50}
        score, ok = layer._distribution_distance(d, dict(d))
        assert ok
        assert score == 0.0

    def test_score_is_symmetric(self):
        # JS distance is a metric — d(P, Q) must equal d(Q, P).
        layer = CorticalLayer(min_drift_samples=10)
        p = {"a": 70, "b": 30}
        q = {"a": 20, "b": 80}
        forward, _ = layer._distribution_distance(p, q)
        reverse, _ = layer._distribution_distance(q, p)
        assert forward == reverse

    def test_score_is_bounded_in_unit_interval(self):
        layer = CorticalLayer(min_drift_samples=10)
        p = {"a": 100}
        q = {"b": 100}  # disjoint support
        score, ok = layer._distribution_distance(p, q)
        assert ok
        assert 0.0 <= score <= 1.0
        # Disjoint distributions sit near the upper end of [0, 1].
        assert score > 0.5

    def test_insufficient_samples_returns_ok_false(self):
        layer = CorticalLayer(min_drift_samples=20)
        p = {"a": 5}
        q = {"a": 4}
        score, ok = layer._distribution_distance(p, q)
        assert ok is False
        assert score == 0.0

    def test_sample_size_invariant_under_scaling(self):
        # Two distributions with the same shape but different volume
        # should yield the same distance.
        layer = CorticalLayer(min_drift_samples=5)
        p_small = {"a": 30, "b": 70}
        q_small = {"a": 70, "b": 30}
        p_big = {k: v * 10 for k, v in p_small.items()}
        q_big = {k: v * 10 for k, v in q_small.items()}
        s1, _ = layer._distribution_distance(p_small, q_small)
        s2, _ = layer._distribution_distance(p_big, q_big)
        # Laplace smoothing introduces a small dependence on N; assert
        # the two are within 0.05.
        assert abs(s1 - s2) < 0.05


class TestKlAlias:
    def test_legacy_alias_routes_to_js_distance(self):
        layer = CorticalLayer(min_drift_samples=10)
        d = {"a": 50, "b": 50}
        legacy = layer._kl_divergence_approx(d, dict(d))
        assert legacy == 0.0

    def test_legacy_alias_clamps_at_one(self):
        layer = CorticalLayer(min_drift_samples=10)
        legacy = layer._kl_divergence_approx({"a": 100}, {"b": 100})
        assert 0.0 <= legacy <= 1.0


class TestDriftIntegration:
    def test_clean_drift_passes(self):
        layer = _layer_with_baseline({"search": 50, "respond": 50})
        result = layer.evaluate(
            _profile(),
            {"action_distribution": {"search": 49, "respond": 51}},
        )
        # Tiny shift — no drift_score above tolerance.
        drift = result.details["drift"]
        assert "action_distribution" in drift["dimensions"]
        assert drift["dimensions"]["action_distribution"] < 0.15

    def test_action_drift_flagged(self):
        layer = _layer_with_baseline({"search": 90, "respond": 10})
        result = layer.evaluate(
            _profile(),
            {"action_distribution": {"search": 10, "respond": 90}},
        )
        drift = result.details["drift"]
        assert "action_distribution" in drift["drifted_dimensions"]
        # JS distance for 90/10 vs 10/90 is well above the 0.15 default.
        assert drift["drift_score"] > 0.15

    def test_below_min_samples_does_not_flag_drift(self):
        # 3 vs 5 observations — below default min_drift_samples=20.
        layer = _layer_with_baseline({"search": 2, "respond": 1})
        result = layer.evaluate(
            _profile(),
            {"action_distribution": {"search": 1, "respond": 4}},
        )
        drift = result.details["drift"]
        assert "action_distribution" not in drift["drifted_dimensions"]
        # Insufficient marker is surfaced for downstream telemetry.
        assert "action_distribution__insufficient_samples" in drift["dimensions"]

    def test_min_samples_threshold_configurable(self):
        layer = CorticalLayer(drift_tolerance=0.15, min_drift_samples=2)
        layer._baseline = BehavioralBaseline(
            agent_id="t",
            action_distribution={"search": 5, "respond": 0},
            sample_count=5,
        )
        result = layer.evaluate(
            _profile(),
            {"action_distribution": {"search": 0, "respond": 5}},
        )
        drift = result.details["drift"]
        assert drift["drift_score"] > 0.0

    def test_no_baseline_returns_zero_drift(self):
        layer = CorticalLayer()  # no baseline
        result = layer.evaluate(_profile(), {})
        drift = result.details["drift"]
        assert drift["drift_score"] == 0.0


class TestKlMonotonicity:
    """Sanity: as a distribution moves away from baseline, distance grows."""

    def test_monotonic_drift(self):
        layer = CorticalLayer(min_drift_samples=10)
        baseline = {"a": 100, "b": 100}
        scores: list[float] = []
        # b strictly grows away from baseline's b=100 — distance must
        # be non-decreasing. At b=100 the distribution equals baseline.
        for b in (100, 120, 140, 160, 180, 199):
            current = {"a": 200 - b, "b": b}
            score, ok = layer._distribution_distance(baseline, current)
            assert ok
            scores.append(score)
        for prev, nxt in zip(scores, scores[1:]):
            assert nxt >= prev - 1e-9
        # And the sequence actually moved.
        assert scores[-1] > scores[0]
        assert math.isfinite(scores[-1])
