"""Integration tests for CorticalLayer + BaselineStore.

The end-to-end promise: a baseline updated in process A and persisted
to a file/sqlite store is restored automatically when process B
constructs a fresh CorticalLayer for the same agent_id. Without
persistence, every restart wipes drift detection until enough new
observations accumulate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers.baseline_store import (
    FileBaselineStore,
    InMemoryBaselineStore,
    SqliteBaselineStore,
)
from agentegrity.layers.cortical import BehavioralBaseline, CorticalLayer


def _profile(agent_id: str = "persistent-agent") -> AgentProfile:
    return AgentProfile(
        name=agent_id,
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
        agent_id=agent_id,
    )


def _populate(layer: CorticalLayer, n: int = 25) -> None:
    """Drive update_baseline n times so we cross the min_drift_samples
    threshold on at least the action distribution."""
    for _ in range(n):
        layer.update_baseline({"action": "search", "tool": "search_api"})
    layer.update_baseline({"action": "respond"})


class TestWriteThrough:
    def test_update_baseline_writes_through(self) -> None:
        store = InMemoryBaselineStore()
        layer = CorticalLayer(baseline_store=store)
        # Trigger evaluate so the layer creates / loads a baseline keyed
        # to the profile's agent_id.
        layer.evaluate(_profile("a1"))
        layer.update_baseline({"action": "search", "tool": "search_api"})
        loaded = store.load("a1")
        assert loaded is not None
        assert loaded.sample_count >= 1
        assert loaded.action_distribution.get("search", 0) >= 1
        assert loaded.tool_usage_patterns.get("search_api", 0) >= 1

    def test_no_store_means_no_writethrough(self) -> None:
        layer = CorticalLayer()
        layer.evaluate(_profile("a2"))
        layer.update_baseline({"action": "search"})
        assert layer._baseline is not None
        assert layer._baseline.sample_count == 1


class TestReadThrough:
    def test_load_on_first_evaluate(self, tmp_path: Path) -> None:
        store = FileBaselineStore(tmp_path)
        # Process A: populate + write through.
        layer_a = CorticalLayer(baseline_store=store)
        profile = _profile("read-back")
        layer_a.evaluate(profile)
        _populate(layer_a, n=30)

        # Confirm the file landed on disk.
        assert (tmp_path / "read-back.json").exists()

        # Process B: fresh layer instance — it should pick up the
        # persisted baseline on first evaluate.
        layer_b = CorticalLayer(baseline_store=store)
        layer_b.evaluate(profile)
        assert layer_b._baseline is not None
        assert layer_b._baseline.agent_id == "read-back"
        assert layer_b._baseline.sample_count >= 30
        assert layer_b._baseline.action_distribution["search"] >= 25

    def test_explicit_baseline_overrides_store(self, tmp_path: Path) -> None:
        # If the caller passes baseline= explicitly, it MUST take
        # precedence over whatever the store contains. This matters
        # when an operator wants to roll back to a known-good
        # baseline.
        store = FileBaselineStore(tmp_path)
        store.save(
            BehavioralBaseline(agent_id="explicit", sample_count=999)
        )
        layer = CorticalLayer(
            baseline=BehavioralBaseline(agent_id="explicit", sample_count=1),
            baseline_store=store,
        )
        layer.evaluate(_profile("explicit"))
        assert layer._baseline is not None
        # Explicit baseline retained — store value (999) was NOT loaded.
        assert layer._baseline.sample_count == 1


class TestSurvivesRestartCycle:
    """The headline test: a process restart does not wipe drift state."""

    @pytest.fixture(params=["file", "sqlite"])
    def persistent_store(self, request: pytest.FixtureRequest, tmp_path: Path):
        if request.param == "file":
            return FileBaselineStore(tmp_path / "f")
        return SqliteBaselineStore(tmp_path / "s.db")

    def test_full_restart_cycle(self, persistent_store) -> None:
        profile = _profile("survives")

        # === Process A ===
        layer_a = CorticalLayer(
            drift_tolerance=0.15,
            min_drift_samples=20,
            baseline_store=persistent_store,
        )
        layer_a.evaluate(profile)
        _populate(layer_a, n=40)
        assert layer_a._baseline is not None
        a_samples = layer_a._baseline.sample_count
        assert a_samples >= 40

        # === Process B (restart) ===
        layer_b = CorticalLayer(
            drift_tolerance=0.15,
            min_drift_samples=20,
            baseline_store=persistent_store,
        )
        # Drift would normally need fresh observations to compute. With
        # the persisted baseline restored, the very first evaluate
        # should already have a populated baseline.
        result = layer_b.evaluate(
            profile,
            {
                "action_distribution": {"search": 5, "respond": 25},
            },
        )
        assert layer_b._baseline is not None
        assert layer_b._baseline.sample_count == a_samples
        assert layer_b._baseline.action_distribution["search"] >= 25

        # And drift detection actually runs (returns a real number for
        # action_distribution rather than the "insufficient samples"
        # marker).
        drift = result.details["drift"]
        assert "action_distribution" in drift["dimensions"]
