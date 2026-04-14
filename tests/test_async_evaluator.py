"""Tests for AsyncIntegrityEvaluator."""

import asyncio

import pytest

from agentegrity.core.evaluator import (
    AsyncIntegrityEvaluator,
    LayerResult,
    PropertyWeights,
)
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier


def _make_profile():
    return AgentProfile(
        name="test-agent",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


class SyncTestLayer:
    """A sync layer for testing."""

    def __init__(self, name: str, score: float = 1.0, action: str = "pass"):
        self._name = name
        self._score = score
        self._action = action

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, profile, context=None):
        return LayerResult(
            layer_name=self._name,
            score=self._score,
            passed=self._action == "pass",
            action=self._action,
            details={"coherence_score": self._score} if self._name == "adversarial" else {},
        )


class AsyncTestLayer:
    """An async layer for testing."""

    def __init__(self, name: str, score: float = 1.0, action: str = "pass"):
        self._name = name
        self._score = score
        self._action = action

    @property
    def name(self) -> str:
        return self._name

    async def aevaluate(self, profile, context=None):
        await asyncio.sleep(0.001)  # simulate async work
        return LayerResult(
            layer_name=self._name,
            score=self._score,
            passed=self._action == "pass",
            action=self._action,
            details={"coherence_score": self._score} if self._name == "adversarial" else {},
        )


@pytest.mark.asyncio
class TestAsyncIntegrityEvaluator:
    async def test_basic_async_evaluation(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[AsyncTestLayer("adversarial", 0.95)]
        )
        result = await evaluator.evaluate(_make_profile())
        assert result.composite > 0
        assert len(result.layer_results) == 1

    async def test_sync_layer_wrapped(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[SyncTestLayer("adversarial", 0.90)]
        )
        result = await evaluator.evaluate(_make_profile())
        assert len(result.layer_results) == 1
        assert result.layer_results[0].score == 0.90

    async def test_mixed_sync_async_layers(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[
                SyncTestLayer("adversarial", 0.90),
                AsyncTestLayer("cortical", 0.85),
            ]
        )
        result = await evaluator.evaluate(_make_profile())
        assert len(result.layer_results) == 2

    async def test_fail_fast_stops_on_block(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[
                AsyncTestLayer("adversarial", 0.3, action="block"),
                AsyncTestLayer("cortical", 0.95),
            ],
            fail_fast=True,
        )
        result = await evaluator.evaluate(_make_profile())
        assert len(result.layer_results) == 1
        assert result.layer_results[0].action == "block"

    async def test_no_fail_fast_runs_all_parallel(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[
                AsyncTestLayer("adversarial", 0.3, action="block"),
                AsyncTestLayer("cortical", 0.95),
            ],
            fail_fast=False,
        )
        result = await evaluator.evaluate(_make_profile())
        assert len(result.layer_results) == 2

    async def test_custom_weights(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[AsyncTestLayer("adversarial", 0.80)],
            weights=PropertyWeights(
                adversarial_coherence=0.50,
                environmental_portability=0.20,
                verifiable_assurance=0.30,
            ),
        )
        result = await evaluator.evaluate(_make_profile())
        assert result.composite > 0

    async def test_repr(self):
        evaluator = AsyncIntegrityEvaluator(
            layers=[AsyncTestLayer("adversarial")]
        )
        assert "AsyncIntegrityEvaluator" in repr(evaluator)
