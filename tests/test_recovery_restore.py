"""Tamper-then-restore tests for RecoveryLayer.snapshot/restore_to.

The promise: if a chain has been tampered, restore_to(prior_checkpoint)
brings the layer back to a state where verify_chain() returns True
again. Without checkpoint backing, RecoveryLayer can detect compromise
but cannot recover from it. This is the difference between 🟡 Reference
and ✅ Hardened on the STATUS matrix for the recovery layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentegrity.core.attestation import AttestationChain, AttestationRecord
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers.checkpoint import (
    FileCheckpoint,
    InMemoryCheckpoint,
    SqliteCheckpoint,
)
from agentegrity.layers.recovery import RecoveryLayer


def _profile() -> AgentProfile:
    return AgentProfile(
        name="t",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use", "state_restore", "rollback"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _populated_chain(n: int = 3) -> AttestationChain:
    chain = AttestationChain()
    for i in range(n):
        chain.append(
            AttestationRecord(
                agent_id="t",
                integrity_score={"composite": 0.9 - i * 0.05},
                layer_states={},
            )
        )
    return chain


class TestSnapshotRequiresBackend:
    def test_snapshot_without_backend_raises(self):
        layer = RecoveryLayer(chain=_populated_chain())
        with pytest.raises(RuntimeError):
            layer.snapshot(agent_id="t")

    def test_restore_without_backend_raises(self):
        layer = RecoveryLayer(chain=_populated_chain())
        with pytest.raises(RuntimeError):
            layer.restore_to("anything")


class TestRoundTrip:
    def test_snapshot_then_restore_preserves_history(self):
        backend = InMemoryCheckpoint()
        layer = RecoveryLayer(
            chain=_populated_chain(),
            score_history=[0.95, 0.92, 0.90],
            checkpoint=backend,
        )
        cid = layer.snapshot(agent_id="t", metadata={"reason": "manual"})

        # Mutate state, then restore.
        layer._score_history.extend([0.30, 0.20, 0.10])
        snap = layer.restore_to(cid)

        assert snap.checkpoint_id == cid
        assert layer._score_history == [0.95, 0.92, 0.90]
        assert layer._chain is not None
        assert layer._chain.verify_chain()

    def test_snapshot_id_surfaces_in_assessment(self):
        backend = InMemoryCheckpoint()
        layer = RecoveryLayer(
            chain=_populated_chain(),
            checkpoint=backend,
        )
        cid = layer.snapshot(agent_id="t")
        result = layer.evaluate(_profile())
        assert result.details["last_checkpoint_id"] == cid
        assert result.details["checkpoint_count"] == 1


class TestTamperRecover:
    def test_tampered_chain_restores_to_clean_baseline(self):
        backend = InMemoryCheckpoint()
        layer = RecoveryLayer(
            chain=_populated_chain(),
            checkpoint=backend,
        )
        clean_cid = layer.snapshot(agent_id="t", metadata={"phase": "clean"})

        # Pre-restore: chain is intact.
        result_before = layer.evaluate(_profile())
        assert result_before.details["chain_intact"] is True

        # Tamper the chain.
        assert layer._chain is not None
        layer._chain.records[1].chain_previous = "tampered_hash"
        # The mutation goes through the public records property which
        # returns a copy — mutate in place via _records instead.
        layer._chain._records[1].chain_previous = "tampered_hash"

        # Mid: chain is now broken; layer escalates.
        result_mid = layer.evaluate(_profile())
        assert result_mid.details["chain_intact"] is False
        assert result_mid.action == "escalate"

        # Restore to the clean checkpoint.
        layer.restore_to(clean_cid)

        # Post: chain verifies again.
        result_after = layer.evaluate(_profile())
        assert result_after.details["chain_intact"] is True
        assert result_after.action != "escalate"

    def test_restore_to_unknown_id_raises_keyerror(self):
        backend = InMemoryCheckpoint()
        layer = RecoveryLayer(
            chain=_populated_chain(),
            checkpoint=backend,
        )
        layer.snapshot(agent_id="t")
        with pytest.raises(KeyError):
            layer.restore_to("not-a-real-checkpoint-id")


class TestCheckpointAsCapability:
    def test_attached_backend_counted_as_recovery_capability(self):
        backend = InMemoryCheckpoint()
        # Profile WITHOUT any RECOVERY_CAPABILITIES.
        profile = AgentProfile(
            name="bare",
            agent_type=AgentType.TOOL_USING,
            capabilities=["tool_use"],
            deployment_context=DeploymentContext.CLOUD,
            risk_tier=RiskTier.MEDIUM,
        )
        layer_no_backend = RecoveryLayer()
        layer_with_backend = RecoveryLayer(checkpoint=backend)

        no_backend = layer_no_backend.evaluate(profile)
        with_backend = layer_with_backend.evaluate(profile)

        assert "checkpoint" not in no_backend.details["recovery_capabilities_present"]
        assert "checkpoint" in with_backend.details["recovery_capabilities_present"]
        assert with_backend.details["recovery_capable"] is True


class TestBackendInteroperability:
    """The same restore semantics work across every reference backend."""

    @pytest.fixture(params=["memory", "file", "sqlite"])
    def backend(self, request: pytest.FixtureRequest, tmp_path: Path):
        if request.param == "memory":
            return InMemoryCheckpoint()
        if request.param == "file":
            return FileCheckpoint(tmp_path / "ckpt")
        return SqliteCheckpoint(tmp_path / "ckpt.db")

    def test_tamper_recover_cycle(self, backend):
        layer = RecoveryLayer(
            chain=_populated_chain(),
            checkpoint=backend,
        )
        cid = layer.snapshot(agent_id="t")
        # Tamper.
        assert layer._chain is not None
        layer._chain._records[1].chain_previous = "tampered_hash"
        assert not layer._chain.verify_chain()
        # Restore.
        layer.restore_to(cid)
        assert layer._chain.verify_chain()
