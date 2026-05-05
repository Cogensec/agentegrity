"""Performance budget for the integrity layers and the full pipeline.

Pinned to the @pytest.mark.benchmark marker so it's excluded from the
default unit-test run. Run explicitly with::

    pytest -m benchmark -v -k perf_budget

Or as part of the full benchmark suite::

    pytest -m benchmark

Why hand-rolled timing instead of pytest-benchmark: the calibrated
floor we care about is p95 latency over a representative event stream,
which a 200-iteration time.perf_counter() loop measures directly.
pytest-benchmark adds JSON output, regression comparison, and warmup
calibration — none of which we need for a CI gate, and the calibration
loop can be flaky on noisy GitHub-hosted runners.

Budget calibrated against the current implementation on a typical
laptop. CI runners are slower; pinning at 50 ms / 100 ms gives ~5x
headroom over measured locals. Tighten as the implementation tightens.
"""

from __future__ import annotations

import statistics
import time
from typing import Callable

import pytest

from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers import (
    AdversarialLayer,
    CorticalLayer,
    GovernanceLayer,
    RecoveryLayer,
    default_layers,
)

pytestmark = pytest.mark.benchmark


# Calibrated p95 ceilings. The default-pipeline path (no LLM, no
# embedding similarity) should comfortably stay under these on any
# reasonable runner. If a future commit causes p95 to creep up, the
# benchmark fails and the diff has to justify the regression.
PER_LAYER_P95_MS = 50.0
FULL_PIPELINE_P95_MS = 100.0
ITERATIONS = 200


def _profile() -> AgentProfile:
    return AgentProfile(
        name="perf-budget",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use", "memory_access"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _representative_context() -> dict[str, object]:
    """A context dict shaped like a real production agent's: a
    benign user prompt, a couple of memory reads with mixed
    provenance, a tool output, an action distribution, and a few
    retrieved documents. Hits every channel the layers scan.
    """
    return {
        "input": "Summarise the latest sales pipeline numbers.",
        "memory_reads": [
            {"provenance": "verified", "content": "Q4 pipeline up 12% YoY."},
            {"provenance": "external", "content": "Customer churn at 4.1%."},
        ],
        "tool_outputs": [
            {"content": "BigQuery returned 1432 rows in 230ms."},
        ],
        "retrieved_documents": [
            {"content": "Sales playbook v3.2 — pipeline metrics overview."},
        ],
        "peer_messages": [],
        "action_distribution": {"search": 18, "respond": 22},
        "tool_usage": {"bigquery": 14, "slack": 6},
        "action": {"type": "respond"},
    }


def _measure(call: Callable[[], object], n: int = ITERATIONS) -> list[float]:
    """Run ``call`` ``n`` times and return per-call latencies in milliseconds."""
    timings: list[float] = []
    # Warm up once outside the measurement window so any first-call
    # imports / regex compilations don't poison the percentile.
    call()
    for _ in range(n):
        t0 = time.perf_counter()
        call()
        timings.append((time.perf_counter() - t0) * 1000.0)
    return timings


def _percentile(values: list[float], pct: float) -> float:
    """Closest-rank percentile (no interpolation) — adequate for n=200."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(len(s) * pct) - 1))
    return s[k]


def _assert_budget(
    name: str, latencies: list[float], budget_ms: float
) -> None:
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    p99 = _percentile(latencies, 0.99)
    mean = statistics.mean(latencies)
    print(
        f"\n  {name:24s} mean={mean:6.2f}ms  "
        f"p50={p50:6.2f}ms  p95={p95:6.2f}ms  p99={p99:6.2f}ms",
    )
    assert p95 <= budget_ms, (
        f"{name} p95={p95:.2f}ms exceeds budget {budget_ms}ms — "
        f"detail: mean={mean:.2f}ms p50={p50:.2f}ms p99={p99:.2f}ms"
    )


class TestPerLayerBudget:
    """Each layer evaluated in isolation should stay under PER_LAYER_P95_MS."""

    def test_adversarial_layer(self) -> None:
        layer = AdversarialLayer()
        profile, ctx = _profile(), _representative_context()
        latencies = _measure(lambda: layer.evaluate(profile, ctx))
        _assert_budget("AdversarialLayer", latencies, PER_LAYER_P95_MS)

    def test_cortical_layer(self) -> None:
        layer = CorticalLayer()
        profile, ctx = _profile(), _representative_context()
        latencies = _measure(lambda: layer.evaluate(profile, ctx))
        _assert_budget("CorticalLayer", latencies, PER_LAYER_P95_MS)

    def test_governance_layer(self) -> None:
        layer = GovernanceLayer(policy_set="enterprise-default")
        profile, ctx = _profile(), _representative_context()
        latencies = _measure(lambda: layer.evaluate(profile, ctx))
        _assert_budget("GovernanceLayer", latencies, PER_LAYER_P95_MS)

    def test_recovery_layer(self) -> None:
        layer = RecoveryLayer()
        profile, ctx = _profile(), _representative_context()
        latencies = _measure(lambda: layer.evaluate(profile, ctx))
        _assert_budget("RecoveryLayer", latencies, PER_LAYER_P95_MS)


class TestFullPipelineBudget:
    """The full default pipeline (4 layers, fail-fast, no LLM) should
    stay under FULL_PIPELINE_P95_MS — a 2x cushion over per-layer.
    """

    def test_default_pipeline(self) -> None:
        evaluator = IntegrityEvaluator(layers=default_layers())
        profile, ctx = _profile(), _representative_context()
        latencies = _measure(lambda: evaluator.evaluate(profile, ctx))
        _assert_budget(
            "IntegrityEvaluator(default_layers)",
            latencies,
            FULL_PIPELINE_P95_MS,
        )

    def test_default_pipeline_total_latency_field_populated(self) -> None:
        """Sanity: the evaluator records a total_latency_ms on its
        result. If this field disappears the perf budget loses its
        runtime introspection point."""
        evaluator = IntegrityEvaluator(layers=default_layers())
        result = evaluator.evaluate(_profile(), _representative_context())
        assert result.total_latency_ms > 0
        assert result.total_latency_ms < FULL_PIPELINE_P95_MS * 5  # 5x ceiling


class TestPerfBudgetMetadata:
    """Sentinel that fails loudly if a maintainer raises the budget
    without thinking it through. If you genuinely need to raise it,
    update the constants AND mention the new number in the commit
    message so the regression is intentional."""

    def test_per_layer_budget_pinned_at_50ms(self) -> None:
        assert PER_LAYER_P95_MS == 50.0

    def test_full_pipeline_budget_pinned_at_100ms(self) -> None:
        assert FULL_PIPELINE_P95_MS == 100.0

    def test_iteration_count_pinned_at_200(self) -> None:
        assert ITERATIONS == 200
