"""RecoveryLayer.snapshot/restore_to never lose attestation records.

Restoring rolls the layer's score history back to a checkpoint. The chain
is evidence, so it is never rolled back: an intact chain keeps every
record and gains a ``recovery_restore`` decision record. A chain that no
longer verifies is archived in full to the checkpoint backend before the
live chain restarts from the checkpoint's verified records, and the
recovery record cites the archive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentegrity.core.attestation import AttestationChain, AttestationRecord
from agentegrity.core.decision import DecisionRecord
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


def _profile(capabilities: list[str] | None = None) -> AgentProfile:
    return AgentProfile(
        name="t",
        agent_type=AgentType.TOOL_USING,
        capabilities=capabilities or ["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _append(chain: AttestationChain, n: int) -> None:
    for i in range(n):
        chain.append(
            AttestationRecord(
                agent_id="t",
                integrity_score={"composite": 0.9 - i * 0.05},
                layer_states={},
            )
        )


def _populated_chain(n: int = 3) -> AttestationChain:
    chain = AttestationChain()
    _append(chain, n)
    return chain


def _ids(chain: AttestationChain) -> list[str]:
    return [r.record_id for r in chain.records]


def _recovery_record(chain: AttestationChain) -> DecisionRecord:
    last = chain.latest
    assert isinstance(last, DecisionRecord)
    assert last.decision_point == "recovery_restore"
    return last


class TestSnapshotRequiresBackend:
    def test_snapshot_without_backend_raises(self):
        layer = RecoveryLayer(chain=_populated_chain())
        with pytest.raises(RuntimeError):
            layer.snapshot(agent_id="t")

    def test_restore_without_backend_raises(self):
        layer = RecoveryLayer(chain=_populated_chain())
        with pytest.raises(RuntimeError):
            layer.restore_to("anything")


class TestRestoreIntactChain:
    def test_restores_score_history_and_keeps_every_record(self):
        chain = _populated_chain()
        layer = RecoveryLayer(chain=chain, score_history=[0.95, 0.92, 0.90],
                              checkpoint=InMemoryCheckpoint())
        cid = layer.snapshot(agent_id="t", metadata={"reason": "manual"})
        _append(chain, 2)
        before = _ids(chain)
        layer._score_history.extend([0.30, 0.20, 0.10])

        snap = layer.restore_to(cid)

        assert snap.checkpoint_id == cid
        assert layer._score_history == [0.95, 0.92, 0.90]
        assert _ids(chain)[:-1] == before
        assert chain.verify_chain()
        record = _recovery_record(chain)
        assert record.candidate_action["checkpoint_id"] == cid
        assert record.candidate_action["archive_id"] is None

    def test_the_chain_object_is_kept(self):
        chain = _populated_chain()
        layer = RecoveryLayer(chain=chain, checkpoint=InMemoryCheckpoint())
        cid = layer.snapshot(agent_id="t")
        layer.restore_to(cid)
        assert layer._chain is chain

    def test_a_checkpoint_from_another_chain_is_refused(self):
        backend = InMemoryCheckpoint()
        other = RecoveryLayer(chain=_populated_chain(), checkpoint=backend)
        cid = other.snapshot(agent_id="t")
        layer = RecoveryLayer(chain=_populated_chain(), checkpoint=backend)
        with pytest.raises(ValueError, match="history"):
            layer.restore_to(cid)

    def test_without_a_chain_the_checkpoint_chain_is_adopted(self):
        backend = InMemoryCheckpoint()
        cid = RecoveryLayer(chain=_populated_chain(), checkpoint=backend).snapshot(agent_id="t")
        layer = RecoveryLayer(checkpoint=backend)
        layer.restore_to(cid)
        assert layer._chain is not None
        assert len(layer._chain) == 4
        assert layer._chain.verify_chain()

    def test_snapshot_id_surfaces_in_assessment(self):
        layer = RecoveryLayer(chain=_populated_chain(), checkpoint=InMemoryCheckpoint())
        cid = layer.snapshot(agent_id="t")
        result = layer.evaluate(_profile())
        assert result.details["last_checkpoint_id"] == cid
        assert result.details["checkpoint_count"] == 1

    def test_the_recovery_record_is_signed_with_the_given_key(self):
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        chain = _populated_chain()
        layer = RecoveryLayer(chain=chain, checkpoint=InMemoryCheckpoint())
        cid = layer.snapshot(agent_id="t")
        layer.restore_to(cid, signing_key=Ed25519PrivateKey.generate())
        assert _recovery_record(chain).verify()


class TestRestoreTamperedChain:
    def _tampered(self, backend):
        chain = _populated_chain()
        layer = RecoveryLayer(chain=chain, checkpoint=backend)
        clean_cid = layer.snapshot(agent_id="t", metadata={"phase": "clean"})
        _append(chain, 2)
        chain._records[1].chain_previous = "tampered_hash"
        return layer, chain, clean_cid

    def test_tamper_escalates_then_restore_verifies(self):
        layer, chain, clean_cid = self._tampered(InMemoryCheckpoint())
        mid = layer.evaluate(_profile())
        assert mid.details["chain_intact"] is False
        assert mid.action == "escalate"

        layer.restore_to(clean_cid)

        after = layer.evaluate(_profile())
        assert after.details["chain_intact"] is True
        assert after.action != "escalate"

    def test_every_pre_restore_record_is_archived(self):
        backend = InMemoryCheckpoint()
        layer, chain, clean_cid = self._tampered(backend)
        before = _ids(chain)

        layer.restore_to(clean_cid)

        record = _recovery_record(chain)
        archive = backend.load(record.candidate_action["archive_id"])
        assert archive is not None
        assert [r["record_id"] for r in archive.chain_records] == before
        assert archive.chain_records[1]["chain_previous"] == "tampered_hash"
        assert record.candidate_action["archived_records"] == 5
        assert archive.metadata["restored_to"] == clean_cid

    def test_the_live_chain_restarts_from_the_checkpoint(self):
        layer, chain, clean_cid = self._tampered(InMemoryCheckpoint())
        clean_ids = _ids(chain)[:3]
        layer.restore_to(clean_cid)
        assert layer._chain is chain
        assert _ids(chain)[:-1] == clean_ids
        assert chain.verify_chain()

    def test_an_archive_is_not_a_restore_point(self):
        backend = InMemoryCheckpoint()
        layer, chain, clean_cid = self._tampered(backend)
        layer.restore_to(clean_cid)
        archive_id = _recovery_record(chain).candidate_action["archive_id"]
        with pytest.raises(ValueError, match="archive"):
            layer.restore_to(archive_id)

    def test_restore_to_unknown_id_raises_keyerror(self):
        layer = RecoveryLayer(chain=_populated_chain(), checkpoint=InMemoryCheckpoint())
        layer.snapshot(agent_id="t")
        with pytest.raises(KeyError):
            layer.restore_to("not-a-real-checkpoint-id")


class TestCapabilitiesNeedAnImplementation:
    def test_declared_capabilities_without_one_score_as_none(self):
        declared = _profile(["tool_use", "state_restore", "checkpoint", "rollback",
                             "session_reset", "peer_quarantine"])
        result = RecoveryLayer().evaluate(declared)
        assert result.details["recovery_capabilities_present"] == []
        assert result.details["recovery_capabilities_unbacked"] == [
            "checkpoint", "peer_quarantine", "rollback", "session_reset", "state_restore",
        ]
        assert result.details["recovery_capable"] is False
        assert result.details["quarantine_capable"] is False
        assert result.score == RecoveryLayer().evaluate(_profile()).score

    def test_an_attached_backend_counts_as_checkpoint(self):
        layer = RecoveryLayer(checkpoint=InMemoryCheckpoint())
        result = layer.evaluate(_profile())
        assert result.details["recovery_capabilities_present"] == ["checkpoint"]
        assert result.details["recovery_capable"] is True
        assert result.score > RecoveryLayer().evaluate(_profile()).score


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
        chain = _populated_chain()
        layer = RecoveryLayer(chain=chain, checkpoint=backend)
        cid = layer.snapshot(agent_id="t")
        before = _ids(chain)
        chain._records[1].chain_previous = "tampered_hash"
        assert not chain.verify_chain()

        layer.restore_to(cid)

        assert chain.verify_chain()
        archive = backend.load(_recovery_record(chain).candidate_action["archive_id"])
        assert [r["record_id"] for r in archive.chain_records] == before
