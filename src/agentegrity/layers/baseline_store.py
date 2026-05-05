"""
Behavioural baseline persistence — survive process restarts.

The :class:`agentegrity.layers.cortical.BehavioralBaseline` lives in
:class:`CorticalLayer` and is updated incrementally during normal
operation (action distribution, tool usage, response characteristics).
Without persistence, a process restart wipes the baseline and the
drift-detection metric falls back to "0.0 — insufficient samples"
until enough new observations accumulate. That's the difference
between drift detection working continuously across a deploy and
working only between reboots.

The :class:`BaselineStore` Protocol is the persistence contract.
Three reference backends ship in this module — pick the one that
matches your operational constraints, or implement the four-method
Protocol against any external store (Redis, S3, Postgres) without
needing to monkey-patch the layer.

The shape mirrors :mod:`agentegrity.layers.checkpoint` so the patterns
rhyme: same atomic-write story for files, same idempotent
``CREATE TABLE IF NOT EXISTS`` story for sqlite, same path-traversal
guard for filesystem ids, same persistent connection for ``:memory:``
sqlite.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from os import fsync, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterator, Protocol, runtime_checkable

from agentegrity.layers.cortical import BehavioralBaseline


def _serialize(baseline: BehavioralBaseline) -> dict[str, Any]:
    """``BehavioralBaseline.to_dict`` returns ISO timestamp; that's the
    canonical wire form. We just delegate."""
    return baseline.to_dict()


def _deserialize(data: dict[str, Any]) -> BehavioralBaseline:
    created_at = data.get("created_at")
    if isinstance(created_at, str):
        created_dt = datetime.fromisoformat(created_at)
    else:
        created_dt = datetime.now(timezone.utc)
    return BehavioralBaseline(
        agent_id=data["agent_id"],
        action_distribution=dict(data.get("action_distribution", {})),
        tool_usage_patterns=dict(data.get("tool_usage_patterns", {})),
        response_length_mean=float(data.get("response_length_mean", 0.0)),
        response_length_std=float(data.get("response_length_std", 0.0)),
        reasoning_depth_mean=float(data.get("reasoning_depth_mean", 0.0)),
        created_at=created_dt,
        sample_count=int(data.get("sample_count", 0)),
    )


@runtime_checkable
class BaselineStore(Protocol):
    """Persistence contract for :class:`BehavioralBaseline` objects.

    A conforming backend MUST guarantee that ``load(agent_id)`` returns
    a value-equal baseline to whatever was last written by
    ``save(agent_id, baseline)``. The ``agent_id`` field on the
    baseline is the canonical key — backends MUST NOT mutate it.
    Multiple ``save`` calls for the same agent_id replace the prior
    record (last-write-wins). ``list_agent_ids`` returns ids in
    insertion order; subsequent ``save`` calls for the same id do not
    move it to the end of the list.
    """

    def save(self, baseline: BehavioralBaseline) -> None: ...
    def load(self, agent_id: str) -> BehavioralBaseline | None: ...
    def list_agent_ids(self) -> list[str]: ...
    def delete(self, agent_id: str) -> bool: ...


class InMemoryBaselineStore:
    """Process-local dict backend.

    Useful for tests and short-lived agents where baselines don't need
    to survive a restart. Insertion order is preserved.
    """

    def __init__(self) -> None:
        self._store: dict[str, BehavioralBaseline] = {}

    def save(self, baseline: BehavioralBaseline) -> None:
        self._store[baseline.agent_id] = baseline

    def load(self, agent_id: str) -> BehavioralBaseline | None:
        return self._store.get(agent_id)

    def list_agent_ids(self) -> list[str]:
        return list(self._store.keys())

    def delete(self, agent_id: str) -> bool:
        return self._store.pop(agent_id, None) is not None


class FileBaselineStore:
    """One JSON file per baseline under a directory.

    Files are written atomically via tempfile + ``os.replace`` so a
    crash mid-write can't leave the store in an inconsistent state.
    Filenames are ``<agent_id>.json``; the directory is created on
    first use.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, agent_id: str) -> Path:
        if "/" in agent_id or "\\" in agent_id or ".." in agent_id:
            raise ValueError(f"invalid agent_id: {agent_id!r}")
        return self._root / f"{agent_id}.json"

    def save(self, baseline: BehavioralBaseline) -> None:
        path = self._path_for(baseline.agent_id)
        payload = json.dumps(_serialize(baseline), sort_keys=True, indent=2)
        with NamedTemporaryFile(
            "w",
            dir=self._root,
            delete=False,
            prefix=f".{baseline.agent_id}-",
            suffix=".tmp",
            encoding="utf-8",
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        replace(tmp_path, path)

    def load(self, agent_id: str) -> BehavioralBaseline | None:
        path = self._path_for(agent_id)
        if not path.exists():
            return None
        return _deserialize(json.loads(path.read_text(encoding="utf-8")))

    def list_agent_ids(self) -> list[str]:
        files = list(self._root.glob("*.json"))
        files.sort(key=lambda p: p.stat().st_mtime)
        return [p.stem for p in files]

    def delete(self, agent_id: str) -> bool:
        path = self._path_for(agent_id)
        if path.exists():
            path.unlink()
            return True
        return False


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS baselines (
    agent_id      TEXT PRIMARY KEY,
    payload       TEXT NOT NULL,
    inserted_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_baselines_inserted_at ON baselines(inserted_at);
"""


class SqliteBaselineStore:
    """sqlite-backed baseline store.

    Single ``baselines`` table keyed by ``agent_id``, payload stored as
    JSON in a TEXT column. Idempotent ``CREATE TABLE IF NOT EXISTS`` so
    reopening an existing file is safe; ``:memory:`` is supported via
    a persistent connection (otherwise each call would get its own
    private database).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._persistent: sqlite3.Connection | None = None
        if self._path == ":memory:":
            self._persistent = sqlite3.connect(self._path)
            self._persistent.row_factory = sqlite3.Row
        with self._open() as conn:
            conn.executescript(_SQLITE_SCHEMA)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        if self._persistent is not None:
            yield self._persistent
            return
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def save(self, baseline: BehavioralBaseline) -> None:
        payload = json.dumps(_serialize(baseline), sort_keys=True)
        with self._open() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO baselines (agent_id, payload) "
                "VALUES (?, ?)",
                (baseline.agent_id, payload),
            )
            conn.commit()

    def load(self, agent_id: str) -> BehavioralBaseline | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT payload FROM baselines WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        if row is None:
            return None
        return _deserialize(json.loads(row["payload"]))

    def list_agent_ids(self) -> list[str]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT agent_id FROM baselines "
                "ORDER BY inserted_at ASC, rowid ASC"
            ).fetchall()
        return [r["agent_id"] for r in rows]

    def delete(self, agent_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM baselines WHERE agent_id = ?", (agent_id,)
            )
            conn.commit()
            return cur.rowcount > 0


__all__ = [
    "BaselineStore",
    "InMemoryBaselineStore",
    "FileBaselineStore",
    "SqliteBaselineStore",
]
