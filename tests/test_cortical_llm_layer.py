"""Tests for the LLM-backed CorticalLLMLayer + default_layers(prefer_llm=).

These tests don't make real Anthropic API calls — every test patches
the underlying SemanticReasoningValidator / SemanticMemoryProvenance-
Checker / SemanticDriftDetector to return canned assessments. That's
the right boundary: the framework's job is to compose pattern-based
and LLM verdicts conservatively, not to test Claude's responses.

A separate live-API integration suite (gated on ANTHROPIC_API_KEY +
``[llm]`` extra) would belong under ``tests/integration/`` if/when
we ship one.
"""

from __future__ import annotations

import importlib.util

import pytest

from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers import default_layers
from agentegrity.layers.cortical import (
    CorticalLayer,
    DriftAssessment,
    MemoryAssessment,
    ReasoningAssessment,
)


def _profile() -> AgentProfile:
    return AgentProfile(
        name="t",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


anthropic_installed = importlib.util.find_spec("anthropic") is not None
pytestmark_no_llm = pytest.mark.skipif(
    not anthropic_installed,
    reason="anthropic not installed; `pip install agentegrity[llm]` to enable",
)


class TestDefaultLayersPreferLlm:
    """The opt-in switch in default_layers()."""

    def test_default_returns_pattern_based_cortical(self):
        layers = default_layers()
        # 4 layers: adversarial, cortical, governance, recovery
        assert [layer.name for layer in layers] == [
            "adversarial",
            "cortical",
            "governance",
            "recovery",
        ]
        # Pattern-based by default — explicitly NOT the LLM subclass.
        assert type(layers[1]).__name__ == "CorticalLayer"

    def test_prefer_llm_false_explicitly(self):
        # Sanity: prefer_llm=False is identical to the no-arg default.
        layers = default_layers(prefer_llm=False)
        assert type(layers[1]).__name__ == "CorticalLayer"

    @pytestmark_no_llm
    def test_prefer_llm_true_returns_llm_layer(self):
        layers = default_layers(prefer_llm=True)
        # CorticalLLMLayer extends CorticalLayer so isinstance still
        # passes — but the runtime type must be the LLM subclass.
        assert type(layers[1]).__name__ == "CorticalLLMLayer"
        assert isinstance(layers[1], CorticalLayer)

    def test_prefer_llm_true_without_anthropic_raises(self, monkeypatch):
        # Force the anthropic import to fail even if the package is
        # installed in the test env, so we exercise the error path.
        import sys

        original_anthropic = sys.modules.get("anthropic")
        monkeypatch.setitem(sys.modules, "anthropic", None)
        try:
            with pytest.raises(ImportError, match="anthropic"):
                default_layers(prefer_llm=True)
        finally:
            if original_anthropic is not None:
                sys.modules["anthropic"] = original_anthropic


@pytestmark_no_llm
class TestCorticalLLMLayerComposition:
    """The conservative-min composition of pattern-based + LLM scores."""

    @pytest.fixture
    def layer(self, monkeypatch):
        from agentegrity.layers.cortical_llm import CorticalLLMLayer

        layer = CorticalLLMLayer(api_key="test-key-not-real")
        return layer

    @pytest.fixture
    def patch_llm_neutral(self, monkeypatch, layer):
        """Stub LLM calls that say "everything's fine" — neutral
        assessments. The composite should equal the pattern-based
        composite because min(base, neutral) == base when neutral is
        the maximum value."""

        async def good_reasoning(*_, **__):
            return ReasoningAssessment(
                consistency_score=1.0, goal_alignment=1.0
            )

        async def good_memory(*_, **__):
            return MemoryAssessment(integrity_score=1.0)

        async def low_drift(*_, **__):
            return DriftAssessment(drift_score=0.0)

        monkeypatch.setattr(layer._reasoning_llm, "analyze", good_reasoning)
        monkeypatch.setattr(layer._memory_llm, "analyze", good_memory)
        monkeypatch.setattr(layer._drift_llm, "analyze", low_drift)
        return layer

    @pytest.fixture
    def patch_llm_alarming(self, monkeypatch, layer):
        """Stub LLM calls that all say "danger" — every dimension low.
        The composite must drop because min() picks the LLM's lower
        scores."""

        async def bad_reasoning(*_, **__):
            return ReasoningAssessment(
                consistency_score=0.10, goal_alignment=0.10
            )

        async def bad_memory(*_, **__):
            return MemoryAssessment(integrity_score=0.10)

        async def high_drift(*_, **__):
            return DriftAssessment(drift_score=0.95)

        monkeypatch.setattr(layer._reasoning_llm, "analyze", bad_reasoning)
        monkeypatch.setattr(layer._memory_llm, "analyze", bad_memory)
        monkeypatch.setattr(layer._drift_llm, "analyze", high_drift)
        return layer

    @pytest.mark.asyncio
    async def test_sync_evaluate_unchanged_no_llm_call(self, layer):
        # Sync path stays pattern-based — no LLM stubs needed because
        # the LLM should never be called here.
        result = layer.evaluate(_profile(), {})
        assert result.layer_name == "cortical"
        assert result.score > 0.0
        # No 'llm' key in details for the sync path.
        assert "llm" not in result.details

    @pytest.mark.asyncio
    async def test_async_with_neutral_llm_matches_pattern_score(
        self, patch_llm_neutral
    ):
        layer = patch_llm_neutral
        sync_result = layer.evaluate(_profile(), {})
        async_result = await layer.aevaluate(_profile(), {})
        # Neutral LLM verdicts can only equal or worsen the pattern
        # composite — they can't make it better. With every LLM
        # dimension at the neutral max, async == sync (modulo float
        # rounding from the recomputation).
        assert abs(async_result.score - sync_result.score) <= 0.0001
        # And the LLM details *are* surfaced.
        assert "llm" in async_result.details

    @pytest.mark.asyncio
    async def test_async_with_alarming_llm_drops_composite(
        self, patch_llm_alarming
    ):
        layer = patch_llm_alarming
        sync_result = layer.evaluate(_profile(), {})
        async_result = await layer.aevaluate(_profile(), {})
        # The LLM said "everything's bad" — composite must drop.
        assert async_result.score < sync_result.score
        # Action should escalate from pass since composite fell below
        # the pattern-based score.
        assert async_result.action != "pass"
        assert not async_result.passed
