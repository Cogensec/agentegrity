"""Tests for the BaselineStore Protocol and the three reference backends.

The Protocol contract is exercised against every backend through one
shared parametrised matrix; backend-specific guarantees (file
atomicity, sqlite schema reuse) get their own targeted tests below.

The end-to-end "survives a process restart" check is in
``test_cortical_baseline_persistence.py`` — that's the integration
test for the wire-through to ``CorticalLayer``.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentegrity.layers.baseline_store import (
    BaselineStore,
    FileBaselineStore,
    InMemoryBaselineStore,
    SqliteBaselineStore,
)
from agentegrity.layers.cortical import BehavioralBaseline


def _baseline(agent_id: str = "agent-1", **overrides: object) -> BehavioralBaseline:
    b = BehavioralBaseline(
        agent_id=agent_id,
        action_distribution={"search": 12, "respond": 8},
        tool_usage_patterns={"calculator": 5, "search_api": 7},
        response_length_mean=420.5,
        response_length_std=85.0,
        reasoning_depth_mean=3.2,
        created_at=datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        sample_count=20,
    )
    for k, v in overrides.items():
        setattr(b, k, v)
    return b


@pytest.fixture
def in_memory() -> InMemoryBaselineStore:
    return InMemoryBaselineStore()


@pytest.fixture
def file_backend(tmp_path: Path) -> FileBaselineStore:
    return FileBaselineStore(tmp_path / "baselines")


@pytest.fixture
def sqlite_backend(tmp_path: Path) -> SqliteBaselineStore:
    return SqliteBaselineStore(tmp_path / "baselines.db")


@pytest.fixture(params=["in_memory", "file_backend", "sqlite_backend"])
def backend(request: pytest.FixtureRequest) -> BaselineStore:
    return request.getfixturevalue(request.param)


class TestProtocolConformance:
    def test_satisfies_protocol(self, backend: BaselineStore) -> None:
        assert isinstance(backend, BaselineStore)

    def test_load_missing_returns_none(self, backend: BaselineStore) -> None:
        assert backend.load("does-not-exist") is None

    def test_save_then_load_round_trips(self, backend: BaselineStore) -> None:
        b = _baseline()
        backend.save(b)
        loaded = backend.load(b.agent_id)
        assert loaded is not None
        assert loaded.agent_id == b.agent_id
        assert loaded.action_distribution == b.action_distribution
        assert loaded.tool_usage_patterns == b.tool_usage_patterns
        assert loaded.response_length_mean == b.response_length_mean
        assert loaded.response_length_std == b.response_length_std
        assert loaded.reasoning_depth_mean == b.reasoning_depth_mean
        assert loaded.sample_count == b.sample_count
        assert loaded.created_at == b.created_at

    def test_save_replaces_prior_record(self, backend: BaselineStore) -> None:
        # Same agent_id, second write wins.
        backend.save(_baseline(agent_id="dup", sample_count=5))
        backend.save(_baseline(agent_id="dup", sample_count=99))
        loaded = backend.load("dup")
        assert loaded is not None
        assert loaded.sample_count == 99

    def test_list_agent_ids_preserves_insertion_order(
        self, backend: BaselineStore
    ) -> None:
        ids = ["alpha", "beta", "gamma"]
        for aid in ids:
            backend.save(_baseline(agent_id=aid))
        assert backend.list_agent_ids() == ids

    def test_delete_removes_record(self, backend: BaselineStore) -> None:
        backend.save(_baseline(agent_id="x"))
        assert backend.delete("x") is True
        assert backend.load("x") is None
        assert backend.delete("x") is False  # idempotent — second delete is no-op

    def test_delete_unknown_returns_false(self, backend: BaselineStore) -> None:
        assert backend.delete("never-existed") is False


class TestFileBackendSpecifics:
    def test_creates_root_dir(self, tmp_path: Path) -> None:
        root = tmp_path / "deep" / "nested" / "baselines"
        store = FileBaselineStore(root)
        store.save(_baseline())
        assert root.exists()
        assert any(root.glob("*.json"))

    def test_rejects_path_traversal_id(self, tmp_path: Path) -> None:
        store = FileBaselineStore(tmp_path)
        with pytest.raises(ValueError):
            store.save(_baseline(agent_id="../escape"))

    def test_rejects_empty_agent_id(self, tmp_path: Path) -> None:
        # Audit M5: an empty id slipped the old block-list and wrote to
        # ".json", colliding across all empty-id agents.
        store = FileBaselineStore(tmp_path)
        with pytest.raises(ValueError):
            store.save(_baseline(agent_id=""))

    def test_rejects_separator_collision(self, tmp_path: Path) -> None:
        # Audit M5: agent_id "a" + role "b" must not collide with
        # agent_id "a__b" + role None on disk.
        store = FileBaselineStore(tmp_path)
        with pytest.raises(ValueError):
            store.save(_baseline(agent_id="a__b"))

    def test_rejects_separator_in_role(self, tmp_path: Path) -> None:
        store = FileBaselineStore(tmp_path)
        with pytest.raises(ValueError):
            store.save(_baseline(agent_id="agent"), role="r__x")

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")
    def test_freshly_created_root_is_private(self, tmp_path: Path) -> None:
        # Audit L3: a store dir we create is owner-only.
        root = tmp_path / "baselines"
        FileBaselineStore(root)
        mode = stat.S_IMODE(root.stat().st_mode)
        assert mode & 0o077 == 0

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        # Audit L4: read-then-catch, no exists() race.
        store = FileBaselineStore(tmp_path)
        assert store.load("nobody") is None

    def test_delete_missing_returns_false(self, tmp_path: Path) -> None:
        # Audit L4: unlink-then-catch, no exists() race.
        store = FileBaselineStore(tmp_path)
        assert store.delete("nobody") is False

    def test_no_temp_files_left_after_save(self, tmp_path: Path) -> None:
        store = FileBaselineStore(tmp_path)
        for i in range(5):
            store.save(_baseline(agent_id=f"a{i}"))
        json_files = list(tmp_path.glob("*.json"))
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(json_files) == 5
        assert tmp_files == []

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        s1 = FileBaselineStore(tmp_path)
        s1.save(_baseline(agent_id="cross"))
        s2 = FileBaselineStore(tmp_path)
        loaded = s2.load("cross")
        assert loaded is not None
        assert loaded.sample_count == 20

    def test_payload_is_pretty_json(self, tmp_path: Path) -> None:
        store = FileBaselineStore(tmp_path)
        store.save(_baseline(agent_id="readable"))
        text = (tmp_path / "readable.json").read_text(encoding="utf-8")
        assert "\n  " in text
        json.loads(text)


class TestSqliteBackendSpecifics:
    def test_persists_across_instances(self, tmp_path: Path) -> None:
        db = tmp_path / "b.db"
        s1 = SqliteBaselineStore(db)
        s1.save(_baseline(agent_id="cross"))
        s2 = SqliteBaselineStore(db)
        loaded = s2.load("cross")
        assert loaded is not None
        assert loaded.sample_count == 20

    def test_in_memory_db(self) -> None:
        store = SqliteBaselineStore(":memory:")
        store.save(_baseline(agent_id="mem"))
        loaded = store.load("mem")
        assert loaded is not None
        assert loaded.agent_id == "mem"

    def test_replace_on_duplicate_id(self, tmp_path: Path) -> None:
        store = SqliteBaselineStore(tmp_path / "b.db")
        store.save(_baseline(agent_id="dup", sample_count=5))
        store.save(_baseline(agent_id="dup", sample_count=99))
        loaded = store.load("dup")
        assert loaded is not None
        assert loaded.sample_count == 99
        assert store.list_agent_ids() == ["dup"]


# --- v0.8: per-role baseline persistence ---


class TestPerRoleBaselines:
    """Each of the 3 backends keys baselines by (agent_id, role)
    with backward-compat fallback to role=None for legacy entries."""

    @pytest.fixture(
        params=["memory", "file", "sqlite"], ids=["memory", "file", "sqlite"]
    )
    def store(
        self, request: pytest.FixtureRequest, tmp_path: Path
    ) -> BaselineStore:
        kind = request.param
        if kind == "memory":
            return InMemoryBaselineStore()
        if kind == "file":
            return FileBaselineStore(tmp_path / "store")
        return SqliteBaselineStore(tmp_path / "b.db")

    def test_role_keyed_save_and_load(self, store: BaselineStore) -> None:
        store.save(_baseline(agent_id="a", sample_count=10), role="leader")
        store.save(_baseline(agent_id="a", sample_count=99), role="worker")
        leader = store.load("a", role="leader")
        worker = store.load("a", role="worker")
        assert leader is not None
        assert worker is not None
        assert leader.sample_count == 10
        assert worker.sample_count == 99

    def test_load_with_role_falls_back_to_legacy(
        self, store: BaselineStore
    ) -> None:
        """A pre-v0.8 baseline (saved without a role) is returned for
        role-keyed lookups when no role-specific entry exists."""
        store.save(_baseline(agent_id="legacy", sample_count=42))
        # No "leader" baseline written; fallback returns the role-less.
        loaded = store.load("legacy", role="leader")
        assert loaded is not None
        assert loaded.sample_count == 42

    def test_role_specific_does_not_shadow_other_roles(
        self, store: BaselineStore
    ) -> None:
        store.save(_baseline(agent_id="b"), role=None)
        store.save(_baseline(agent_id="b", sample_count=77), role="supervisor")
        # role=None remains its own entry
        roleless = store.load("b", role=None)
        assert roleless is not None
        assert roleless.sample_count == 20
        # supervisor is distinct
        sup = store.load("b", role="supervisor")
        assert sup is not None
        assert sup.sample_count == 77

    def test_list_keys_returns_role_pairs(self, store: BaselineStore) -> None:
        store.save(_baseline(agent_id="a"), role=None)
        store.save(_baseline(agent_id="a"), role="leader")
        store.save(_baseline(agent_id="b"), role="peer")
        keys = set(store.list_keys())
        assert ("a", None) in keys
        assert ("a", "leader") in keys
        assert ("b", "peer") in keys

    def test_list_agent_ids_deduplicates_across_roles(
        self, store: BaselineStore
    ) -> None:
        store.save(_baseline(agent_id="a"), role=None)
        store.save(_baseline(agent_id="a"), role="leader")
        store.save(_baseline(agent_id="b"), role="peer")
        ids = store.list_agent_ids()
        # Each agent_id appears once regardless of how many roles it has.
        assert sorted(ids) == ["a", "b"]

    def test_delete_targets_specific_role(self, store: BaselineStore) -> None:
        store.save(_baseline(agent_id="a"), role=None)
        store.save(_baseline(agent_id="a"), role="leader")
        deleted = store.delete("a", role="leader")
        assert deleted is True
        assert store.load("a", role="leader") is not None  # fallback to None
        # But the role=None entry survives.
        assert store.load("a", role=None) is not None
        # And direct query for "leader" without fallback path: NOTE
        # load always falls back, so we need list_keys to assert.
        assert ("a", "leader") not in store.list_keys()
        assert ("a", None) in store.list_keys()

    def test_invalid_role_rejected(self, store: BaselineStore) -> None:
        with pytest.raises(ValueError, match="invalid role"):
            store.save(_baseline(agent_id="a"), role="../etc/passwd")
        with pytest.raises(ValueError, match="invalid role"):
            store.load("a", role="../etc/passwd")


class TestSqliteSchemaMigration:
    def test_old_schema_migrated_on_open(self, tmp_path: Path) -> None:
        """A pre-v0.8 SQLite database (no role column) is migrated
        to the composite-PK schema on first v0.8 open. Existing
        rows are preserved with role=''."""
        import sqlite3
        db = tmp_path / "legacy.db"
        # Hand-craft a pre-v0.8 database.
        conn = sqlite3.connect(str(db))
        conn.executescript(
            "CREATE TABLE baselines ("
            "  agent_id TEXT PRIMARY KEY,"
            "  payload TEXT NOT NULL,"
            "  inserted_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ");"
        )
        payload = json.dumps({
            "agent_id": "old-agent",
            "action_distribution": {},
            "tool_usage_patterns": {},
            "response_length_mean": 0.0,
            "response_length_std": 0.0,
            "reasoning_depth_mean": 0.0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "sample_count": 100,
        })
        conn.execute(
            "INSERT INTO baselines (agent_id, payload) VALUES (?, ?)",
            ("old-agent", payload),
        )
        conn.commit()
        conn.close()

        # First v0.8 open triggers migration.
        store = SqliteBaselineStore(db)
        loaded = store.load("old-agent")
        assert loaded is not None
        assert loaded.sample_count == 100
        # Role-keyed lookup falls back to the migrated role='' entry.
        loaded_with_role = store.load("old-agent", role="leader")
        assert loaded_with_role is not None
        assert loaded_with_role.sample_count == 100
