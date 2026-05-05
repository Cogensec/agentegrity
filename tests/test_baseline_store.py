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
