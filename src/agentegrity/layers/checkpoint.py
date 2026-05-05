"""
Recovery checkpoints - persistable snapshots of an agent's recovery state.

A checkpoint captures everything the recovery layer needs to roll the
agent back to a known-good baseline:

  * the attestation chain (signed records, in order)
  * the rolling score history used for sustained-degradation detection
  * an optional behavioural baseline snapshot
  * arbitrary user metadata

The :class:`Checkpoint` Protocol is the contract every backend
satisfies. The framework ships three reference backends:

  * :class:`InMemoryCheckpoint` — dict in process memory; useful for
    tests and short-lived agents.
  * :class:`FileCheckpoint` — one JSON file per checkpoint under a
    directory; survives process restarts and is grep/jq-able.
  * :class:`SqliteCheckpoint` — single sqlite database file; useful
    when you want concurrent readers + indexed lookups without spinning
    up infrastructure.

External backends (S3, Redis, KMS-wrapped, etc.) are out of scope for
the reference implementation but are trivially pluggable: implement the
four-method Protocol and pass the instance to ``RecoveryLayer(checkpoint=...)``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from os import fsync, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass
class CheckpointSnapshot:
    """A point-in-time snapshot of an agent's recovery state.

    Attributes
    ----------
    agent_id : str
        The agent this snapshot belongs to. Backends MAY use this for
        scoping but are not required to enforce isolation.
    checkpoint_id : str
        Stable identifier for this snapshot. Defaults to a fresh UUID.
        A backend's :meth:`Checkpoint.save` MAY return a different id
        (e.g. one it generated itself), in which case the returned id
        is canonical.
    created_at : str
        ISO-8601 UTC timestamp.
    score_history : list[float]
        The rolling history of composite scores. Restored verbatim into
        :attr:`RecoveryLayer._score_history`.
    chain_records : list[dict]
        Each :class:`AttestationRecord` serialised via
        :meth:`AttestationRecord.to_dict`. The chain is rebuilt
        in-order, preserving original ``chain_previous`` link hashes.
    baseline : dict | None
        Optional :class:`BehavioralBaseline` snapshot (``to_dict``).
    metadata : dict
        Arbitrary user-supplied tags. Backends MUST persist this
        verbatim and SHOULD NOT mutate it.
    """

    agent_id: str
    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    score_history: list[float] = field(default_factory=list)
    chain_records: list[dict[str, Any]] = field(default_factory=list)
    baseline: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointSnapshot":
        return cls(
            agent_id=data["agent_id"],
            checkpoint_id=data["checkpoint_id"],
            created_at=data["created_at"],
            score_history=list(data.get("score_history", [])),
            chain_records=list(data.get("chain_records", [])),
            baseline=data.get("baseline"),
            metadata=dict(data.get("metadata", {})),
        )


@runtime_checkable
class Checkpoint(Protocol):
    """Persistence contract for :class:`CheckpointSnapshot` objects.

    A conforming backend MUST guarantee that the snapshot returned by
    :meth:`load` is value-equal to the snapshot passed to :meth:`save`
    apart from possibly-modified ``checkpoint_id``. ``save`` MUST return
    the canonical id for the persisted snapshot. ``list_ids`` MUST
    return ids in insertion order; ``latest`` is a convenience for the
    most-recently-saved snapshot.
    """

    def save(self, snapshot: CheckpointSnapshot) -> str: ...
    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None: ...
    def list_ids(self) -> list[str]: ...
    def latest(self) -> CheckpointSnapshot | None: ...


class InMemoryCheckpoint:
    """In-process dict backend.

    Useful for tests and short-lived agents where checkpoints don't
    need to survive a restart. Insertion order is preserved.
    """

    def __init__(self) -> None:
        self._store: dict[str, CheckpointSnapshot] = {}

    def save(self, snapshot: CheckpointSnapshot) -> str:
        self._store[snapshot.checkpoint_id] = snapshot
        return snapshot.checkpoint_id

    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        return self._store.get(checkpoint_id)

    def list_ids(self) -> list[str]:
        return list(self._store.keys())

    def latest(self) -> CheckpointSnapshot | None:
        if not self._store:
            return None
        return next(reversed(self._store.values()))


class FileCheckpoint:
    """One JSON file per checkpoint under a directory.

    Files are written atomically (temp file + ``os.replace``) so a
    crash mid-write can't leave the store in an inconsistent state.
    Filenames are ``<checkpoint_id>.json``; the directory is created
    on first use if it doesn't exist.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, checkpoint_id: str) -> Path:
        # Refuse path-traversal attempts in the id.
        if "/" in checkpoint_id or "\\" in checkpoint_id or ".." in checkpoint_id:
            raise ValueError(f"invalid checkpoint id: {checkpoint_id!r}")
        return self._root / f"{checkpoint_id}.json"

    def save(self, snapshot: CheckpointSnapshot) -> str:
        path = self._path_for(snapshot.checkpoint_id)
        payload = json.dumps(snapshot.to_dict(), sort_keys=True, indent=2)
        # Atomic write: temp file in the same dir, fsync, then rename.
        with NamedTemporaryFile(
            "w",
            dir=self._root,
            delete=False,
            prefix=f".{snapshot.checkpoint_id}-",
            suffix=".tmp",
            encoding="utf-8",
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        replace(tmp_path, path)
        return snapshot.checkpoint_id

    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        path = self._path_for(checkpoint_id)
        if not path.exists():
            return None
        return CheckpointSnapshot.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )

    def list_ids(self) -> list[str]:
        # Sort by mtime (oldest first) so insertion-order semantics are
        # preserved across process restarts.
        files = [p for p in self._root.glob("*.json")]
        files.sort(key=lambda p: p.stat().st_mtime)
        return [p.stem for p in files]

    def latest(self) -> CheckpointSnapshot | None:
        ids = self.list_ids()
        if not ids:
            return None
        return self.load(ids[-1])


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    inserted_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_agent ON checkpoints(agent_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_inserted_at ON checkpoints(inserted_at);
"""


class SqliteCheckpoint:
    """sqlite-backed checkpoint store.

    A single ``checkpoints`` table with the snapshot payload stored as
    JSON in a TEXT column. Insertion order is recovered from the
    auto-managed ``inserted_at`` column.

    The ``path`` argument is passed through to :func:`sqlite3.connect`,
    so ``":memory:"`` works for tests. Connections are short-lived
    (one per call) so this backend is safe to share across threads.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # For ":memory:" each connection gets its own private database,
        # so we keep one persistent connection for the lifetime of the
        # backend instance. For file-backed paths we open a fresh
        # connection per call (cheap, thread-safe).
        self._persistent: sqlite3.Connection | None = None
        if self._path == ":memory:":
            self._persistent = sqlite3.connect(self._path)
            self._persistent.row_factory = sqlite3.Row
        with self._open() as conn:
            conn.executescript(_SQLITE_SCHEMA)

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        if self._persistent is not None:
            # Persistent connection — never close it on context exit.
            yield self._persistent
            return
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def save(self, snapshot: CheckpointSnapshot) -> str:
        payload = json.dumps(snapshot.to_dict(), sort_keys=True)
        with self._open() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints "
                "(checkpoint_id, agent_id, created_at, payload) "
                "VALUES (?, ?, ?, ?)",
                (
                    snapshot.checkpoint_id,
                    snapshot.agent_id,
                    snapshot.created_at,
                    payload,
                ),
            )
            conn.commit()
        return snapshot.checkpoint_id

    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT payload FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return CheckpointSnapshot.from_dict(json.loads(row["payload"]))

    def list_ids(self) -> list[str]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT checkpoint_id FROM checkpoints "
                "ORDER BY inserted_at ASC, rowid ASC"
            ).fetchall()
        return [r["checkpoint_id"] for r in rows]

    def latest(self) -> CheckpointSnapshot | None:
        ids = self.list_ids()
        if not ids:
            return None
        return self.load(ids[-1])


__all__ = [
    "Checkpoint",
    "CheckpointSnapshot",
    "InMemoryCheckpoint",
    "FileCheckpoint",
    "SqliteCheckpoint",
]
