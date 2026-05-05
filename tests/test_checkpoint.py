"""Tests for the Checkpoint Protocol and the three reference backends.

Each backend is exercised through the same shared test matrix so that a
new backend (S3, Redis, etc.) can adopt the contract by adding one
fixture entry. Backend-specific behaviour (file atomicity, sqlite
schema) gets its own targeted tests below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentegrity.core.attestation import AttestationChain, AttestationRecord
from agentegrity.layers.checkpoint import (
    Checkpoint,
    CheckpointSnapshot,
    FileCheckpoint,
    InMemoryCheckpoint,
    SqliteCheckpoint,
)


def _snapshot(agent_id: str = "agent-1", **overrides: object) -> CheckpointSnapshot:
    chain = AttestationChain()
    for i in range(3):
        chain.append(
            AttestationRecord(
                agent_id=agent_id,
                integrity_score={"composite": 0.9 - i * 0.05},
                layer_states={"adversarial": {"score": 0.95}},
            )
        )
    snap = CheckpointSnapshot(
        agent_id=agent_id,
        score_history=[0.95, 0.91, 0.89],
        chain_records=chain.to_records_dict(),
        baseline={"sample_count": 12, "created_at": "2026-04-30T00:00:00+00:00"},
        metadata={"reason": "test"},
    )
    for k, v in overrides.items():
        setattr(snap, k, v)
    return snap


@pytest.fixture
def in_memory() -> InMemoryCheckpoint:
    return InMemoryCheckpoint()


@pytest.fixture
def file_backend(tmp_path: Path) -> FileCheckpoint:
    return FileCheckpoint(tmp_path / "checkpoints")


@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SqliteCheckpoint:
    return SqliteCheckpoint(tmp_path / "checkpoints.db")


def all_backends(request: pytest.FixtureRequest) -> Checkpoint:
    return request.getfixturevalue(request.param)


@pytest.fixture(params=["in_memory", "file_backend", "sqlite_backend"])
def backend(request: pytest.FixtureRequest) -> Checkpoint:
    return all_backends(request)


class TestProtocolConformance:
    """Every backend must satisfy the Checkpoint Protocol contract."""

    def test_satisfies_protocol(self, backend: Checkpoint):
        assert isinstance(backend, Checkpoint)

    def test_save_returns_snapshot_id(self, backend: Checkpoint):
        snap = _snapshot()
        cid = backend.save(snap)
        assert cid == snap.checkpoint_id

    def test_load_returns_value_equal_snapshot(self, backend: Checkpoint):
        snap = _snapshot()
        backend.save(snap)
        loaded = backend.load(snap.checkpoint_id)
        assert loaded is not None
        assert loaded.checkpoint_id == snap.checkpoint_id
        assert loaded.agent_id == snap.agent_id
        assert loaded.score_history == snap.score_history
        assert loaded.metadata == snap.metadata
        assert len(loaded.chain_records) == len(snap.chain_records)

    def test_load_missing_returns_none(self, backend: Checkpoint):
        assert backend.load("does-not-exist") is None

    def test_list_ids_preserves_insertion_order(self, backend: Checkpoint):
        ids = [backend.save(_snapshot()) for _ in range(3)]
        listed = backend.list_ids()
        assert listed == ids

    def test_latest_returns_most_recent(self, backend: Checkpoint):
        first = backend.save(_snapshot())
        second = backend.save(_snapshot())
        latest = backend.latest()
        assert latest is not None
        assert latest.checkpoint_id == second
        assert latest.checkpoint_id != first

    def test_latest_when_empty(self, backend: Checkpoint):
        assert backend.latest() is None

    def test_chain_records_round_trip_to_verifiable_chain(
        self, backend: Checkpoint
    ):
        snap = _snapshot()
        backend.save(snap)
        loaded = backend.load(snap.checkpoint_id)
        assert loaded is not None
        rebuilt = AttestationChain.from_dict_list(loaded.chain_records)
        assert rebuilt.verify_chain()
        assert len(rebuilt) == 3


class TestFileBackendSpecifics:
    def test_creates_root_dir(self, tmp_path: Path):
        root = tmp_path / "deep" / "nested" / "checkpoints"
        backend = FileCheckpoint(root)
        backend.save(_snapshot())
        assert root.exists()
        assert any(root.glob("*.json"))

    def test_rejects_path_traversal_id(self, tmp_path: Path):
        backend = FileCheckpoint(tmp_path)
        snap = _snapshot()
        snap.checkpoint_id = "../escape"
        with pytest.raises(ValueError):
            backend.save(snap)

    def test_atomic_write_no_partial_files(self, tmp_path: Path):
        # The save() implementation uses temp file + os.replace. After a
        # successful save there should be exactly one *.json per
        # checkpoint and no leftover *.tmp files.
        backend = FileCheckpoint(tmp_path)
        for _ in range(5):
            backend.save(_snapshot())
        json_files = list(tmp_path.glob("*.json"))
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(json_files) == 5
        assert tmp_files == []

    def test_persists_across_instances(self, tmp_path: Path):
        backend1 = FileCheckpoint(tmp_path)
        cid = backend1.save(_snapshot())
        backend2 = FileCheckpoint(tmp_path)
        loaded = backend2.load(cid)
        assert loaded is not None
        assert loaded.checkpoint_id == cid

    def test_payload_is_pretty_json(self, tmp_path: Path):
        backend = FileCheckpoint(tmp_path)
        cid = backend.save(_snapshot())
        path = tmp_path / f"{cid}.json"
        text = path.read_text(encoding="utf-8")
        # Pretty-printed: contains newlines + 2-space indent.
        assert "\n  " in text
        # Round-trips as JSON.
        json.loads(text)


class TestSqliteBackendSpecifics:
    def test_persists_across_instances(self, tmp_path: Path):
        db = tmp_path / "ck.db"
        backend1 = SqliteCheckpoint(db)
        cid = backend1.save(_snapshot())
        backend2 = SqliteCheckpoint(db)
        loaded = backend2.load(cid)
        assert loaded is not None
        assert loaded.checkpoint_id == cid

    def test_in_memory_db(self):
        backend = SqliteCheckpoint(":memory:")
        # An in-memory DB still satisfies the round-trip contract within
        # one process; cross-instance persistence is not promised.
        cid = backend.save(_snapshot())
        loaded = backend.load(cid)
        assert loaded is not None
        assert loaded.checkpoint_id == cid

    def test_replace_on_duplicate_id(self, tmp_path: Path):
        backend = SqliteCheckpoint(tmp_path / "ck.db")
        snap = _snapshot()
        backend.save(snap)
        snap.metadata = {"reason": "updated"}
        backend.save(snap)
        loaded = backend.load(snap.checkpoint_id)
        assert loaded is not None
        assert loaded.metadata == {"reason": "updated"}
        # Only one row, not two.
        assert backend.list_ids() == [snap.checkpoint_id]


class TestCheckpointSnapshotSerialization:
    def test_to_dict_from_dict_round_trip(self):
        snap = _snapshot()
        rebuilt = CheckpointSnapshot.from_dict(snap.to_dict())
        assert rebuilt.checkpoint_id == snap.checkpoint_id
        assert rebuilt.score_history == snap.score_history
        assert rebuilt.metadata == snap.metadata
        assert rebuilt.baseline == snap.baseline
        assert len(rebuilt.chain_records) == len(snap.chain_records)
