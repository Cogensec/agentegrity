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
matches your operational constraints, or implement the Protocol
against any external store (Redis, S3, Postgres) without
needing to monkey-patch the layer.

The shape mirrors :mod:`agentegrity.layers.checkpoint` so the patterns
rhyme: same atomic-write story for files, same idempotent
``CREATE TABLE IF NOT EXISTS`` story for sqlite, same path-traversal
guard for filesystem ids, same persistent connection for ``:memory:``
sqlite.

v0.8: per-role baselines. All four methods grow an optional
``role`` parameter so a multi-agent topology can keep distinct
baselines for the same ``agent_id`` in different ``AgentRole``s
(LEADER vs WORKER). The default ``role=None`` preserves v0.7
single-agent behaviour. ``load`` falls back to the role-less
entry when no role-specific entry exists, so pre-v0.8 baselines
continue to work transparently under role-keyed lookups.
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

from agentegrity.layers.checkpoint import (
    create_private_dir,
    restrict_file,
    validate_storage_identifier,
)
from agentegrity.layers.cortical import BehavioralBaseline

# Sentinel string for SQLite composite-PK rows that represent
# legacy role-less baselines. Composite PK behaviour with NULL
# differs across SQLite versions (some treat NULLs as distinct);
# using a known-empty string avoids the portability hazard.
_NO_ROLE = ""


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


def _validate_role(role: str | None) -> None:
    if role is None:
        return
    validate_storage_identifier(role, kind="role")
    # The "__" separator in role-keyed filenames must be unambiguous, so
    # neither agent_id nor role may itself contain it (otherwise
    # agent="a" role="b" collides with agent="a__b" role=None).
    if "__" in role:
        raise ValueError(f"invalid role {role!r}: must not contain '__'")


@runtime_checkable
class BaselineStore(Protocol):
    """Persistence contract for :class:`BehavioralBaseline` objects.

    A conforming backend MUST guarantee that ``load(agent_id, role)``
    returns a value-equal baseline to whatever was last written by
    ``save(baseline, role)`` for the same ``(agent_id, role)`` key.

    Per-role baselines (v0.8): the optional ``role`` parameter
    distinguishes the same agent in different topology roles
    (e.g., LEADER vs WORKER). ``role=None`` is the legacy
    single-agent key. ``load`` falls back to ``role=None`` when no
    role-specific entry exists, so pre-v0.8 baselines continue
    to serve role-keyed lookups transparently.
    """

    def save(
        self, baseline: BehavioralBaseline, role: str | None = None
    ) -> None: ...
    def load(
        self, agent_id: str, role: str | None = None
    ) -> BehavioralBaseline | None: ...
    def list_agent_ids(self) -> list[str]: ...
    def list_keys(self) -> list[tuple[str, str | None]]: ...
    def delete(self, agent_id: str, role: str | None = None) -> bool: ...


class InMemoryBaselineStore:
    """Process-local dict backend.

    Useful for tests and short-lived agents where baselines don't need
    to survive a restart. Insertion order is preserved across the
    combined ``(agent_id, role)`` keyspace.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str | None], BehavioralBaseline] = {}

    def save(
        self, baseline: BehavioralBaseline, role: str | None = None
    ) -> None:
        _validate_role(role)
        self._store[(baseline.agent_id, role)] = baseline

    def load(
        self, agent_id: str, role: str | None = None
    ) -> BehavioralBaseline | None:
        _validate_role(role)
        if role is not None:
            entry = self._store.get((agent_id, role))
            if entry is not None:
                return entry
            # Fallback: pre-v0.8 baseline stored without a role.
            return self._store.get((agent_id, None))
        return self._store.get((agent_id, None))

    def list_agent_ids(self) -> list[str]:
        seen: list[str] = []
        seen_set: set[str] = set()
        for agent_id, _ in self._store.keys():
            if agent_id not in seen_set:
                seen.append(agent_id)
                seen_set.add(agent_id)
        return seen

    def list_keys(self) -> list[tuple[str, str | None]]:
        return list(self._store.keys())

    def delete(self, agent_id: str, role: str | None = None) -> bool:
        _validate_role(role)
        return self._store.pop((agent_id, role), None) is not None


class FileBaselineStore:
    """One JSON file per baseline under a directory.

    Files are written atomically via tempfile + ``os.replace`` so a
    crash mid-write can't leave the store in an inconsistent state.

    Naming (v0.8):
    - role-less (legacy): ``<agent_id>.json``
    - role-keyed: ``<agent_id>__<role>.json``

    The double-underscore separator is chosen because it cannot
    appear in a sanitized ``agent_id`` (path-traversal-guarded) nor
    in a sanitized ``role`` (alphanumeric + underscore + hyphen
    only, single-underscores allowed), so the encoding is
    unambiguously decodable.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        create_private_dir(self._root)

    def _path_for(self, agent_id: str, role: str | None) -> Path:
        validate_storage_identifier(agent_id, kind="agent_id")
        if "__" in agent_id:
            raise ValueError(
                f"invalid agent_id {agent_id!r}: must not contain '__' "
                f"(reserved as the role-key separator)"
            )
        _validate_role(role)
        if role is None:
            return self._root / f"{agent_id}.json"
        return self._root / f"{agent_id}__{role}.json"

    def save(
        self, baseline: BehavioralBaseline, role: str | None = None
    ) -> None:
        path = self._path_for(baseline.agent_id, role)
        payload = json.dumps(_serialize(baseline), sort_keys=True, indent=2)
        prefix = f".{baseline.agent_id}"
        if role is not None:
            prefix = f"{prefix}__{role}"
        with NamedTemporaryFile(
            "w",
            dir=self._root,
            delete=False,
            prefix=f"{prefix}-",
            suffix=".tmp",
            encoding="utf-8",
        ) as tmp:
            tmp.write(payload)
            tmp.flush()
            fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        replace(tmp_path, path)

    def load(
        self, agent_id: str, role: str | None = None
    ) -> BehavioralBaseline | None:
        # Read-then-catch rather than exists()-then-read, which races
        # (TOCTOU) if the file is removed between the two calls.
        if role is not None:
            role_path = self._path_for(agent_id, role)
            try:
                return _deserialize(
                    json.loads(role_path.read_text(encoding="utf-8"))
                )
            except FileNotFoundError:
                pass  # Fallback to role-less (legacy) entry.
        path = self._path_for(agent_id, None)
        try:
            return _deserialize(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None

    def list_agent_ids(self) -> list[str]:
        files = list(self._root.glob("*.json"))
        files.sort(key=lambda p: p.stat().st_mtime)
        seen: list[str] = []
        seen_set: set[str] = set()
        for p in files:
            stem = p.stem
            # Strip the optional __<role> suffix.
            if "__" in stem:
                agent_id = stem.split("__", 1)[0]
            else:
                agent_id = stem
            if agent_id not in seen_set:
                seen.append(agent_id)
                seen_set.add(agent_id)
        return seen

    def list_keys(self) -> list[tuple[str, str | None]]:
        files = list(self._root.glob("*.json"))
        files.sort(key=lambda p: p.stat().st_mtime)
        out: list[tuple[str, str | None]] = []
        for p in files:
            stem = p.stem
            if "__" in stem:
                agent_id, role = stem.split("__", 1)
                out.append((agent_id, role))
            else:
                out.append((stem, None))
        return out

    def delete(self, agent_id: str, role: str | None = None) -> bool:
        path = self._path_for(agent_id, role)
        # Unlink-then-catch rather than exists()-then-unlink (TOCTOU).
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS baselines (
    agent_id      TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL,
    inserted_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (agent_id, role)
);
CREATE INDEX IF NOT EXISTS idx_baselines_inserted_at ON baselines(inserted_at);
"""


class SqliteBaselineStore:
    """sqlite-backed baseline store.

    Single ``baselines`` table keyed by composite ``(agent_id, role)``,
    payload stored as JSON in a TEXT column. The ``role`` column
    defaults to the empty string for legacy single-agent baselines —
    avoiding SQLite's inconsistent treatment of NULL in composite
    primary keys across versions.

    Idempotent ``CREATE TABLE IF NOT EXISTS`` so reopening an
    existing file is safe. v0.8 schema migration runs on first
    open: a pre-v0.8 database has the original ``agent_id TEXT
    PRIMARY KEY`` schema (no ``role`` column); the
    ``_migrate_schema_if_needed`` helper detects the missing
    column and runs ``ALTER TABLE ADD COLUMN`` + index recreation
    in one transaction.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._persistent: sqlite3.Connection | None = None
        if self._path == ":memory:":
            self._persistent = sqlite3.connect(self._path)
            self._persistent.row_factory = sqlite3.Row
        with self._open() as conn:
            self._migrate_schema_if_needed(conn)
            conn.executescript(_SQLITE_SCHEMA)
        if self._path != ":memory:":
            restrict_file(self._path)

    def _migrate_schema_if_needed(self, conn: sqlite3.Connection) -> None:
        """Add the v0.8 ``role`` column to pre-v0.8 databases.

        Pre-v0.8 schema: ``baselines(agent_id TEXT PRIMARY KEY,
        payload TEXT, inserted_at TEXT)`` — no ``role`` column.
        v0.8 schema needs role as part of the composite PK. The
        cleanest migration is to add the column with a default of
        the empty string (the sentinel for "no role"), then
        recreate the PK by table copy. We only do the copy when
        necessary.
        """
        cur = conn.execute("PRAGMA table_info(baselines)")
        cols = {row[1] for row in cur.fetchall()}
        if not cols:
            # Fresh DB — _SQLITE_SCHEMA creates the right shape.
            return
        if "role" in cols:
            return
        # Pre-v0.8 DB: copy old rows into a new schema and replace.
        with conn:
            conn.execute(
                "CREATE TABLE baselines_v08 ("
                "  agent_id TEXT NOT NULL,"
                "  role TEXT NOT NULL DEFAULT '',"
                "  payload TEXT NOT NULL,"
                "  inserted_at TEXT NOT NULL DEFAULT (datetime('now')),"
                "  PRIMARY KEY (agent_id, role)"
                ")"
            )
            conn.execute(
                "INSERT INTO baselines_v08 (agent_id, role, payload, inserted_at) "
                "SELECT agent_id, '', payload, inserted_at FROM baselines"
            )
            conn.execute("DROP TABLE baselines")
            conn.execute("ALTER TABLE baselines_v08 RENAME TO baselines")

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

    def _encode_role(self, role: str | None) -> str:
        _validate_role(role)
        return role if role is not None else _NO_ROLE

    def _decode_role(self, role: str) -> str | None:
        return None if role == _NO_ROLE else role

    def save(
        self, baseline: BehavioralBaseline, role: str | None = None
    ) -> None:
        payload = json.dumps(_serialize(baseline), sort_keys=True)
        with self._open() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO baselines (agent_id, role, payload) "
                "VALUES (?, ?, ?)",
                (baseline.agent_id, self._encode_role(role), payload),
            )
            conn.commit()

    def load(
        self, agent_id: str, role: str | None = None
    ) -> BehavioralBaseline | None:
        with self._open() as conn:
            if role is not None:
                row = conn.execute(
                    "SELECT payload FROM baselines "
                    "WHERE agent_id = ? AND role = ?",
                    (agent_id, self._encode_role(role)),
                ).fetchone()
                if row is not None:
                    return _deserialize(json.loads(row["payload"]))
                # Fallback to role-less entry.
            row = conn.execute(
                "SELECT payload FROM baselines "
                "WHERE agent_id = ? AND role = ?",
                (agent_id, _NO_ROLE),
            ).fetchone()
        if row is None:
            return None
        return _deserialize(json.loads(row["payload"]))

    def list_agent_ids(self) -> list[str]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT DISTINCT agent_id FROM baselines "
                "ORDER BY inserted_at ASC, rowid ASC"
            ).fetchall()
        return [r["agent_id"] for r in rows]

    def list_keys(self) -> list[tuple[str, str | None]]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT agent_id, role FROM baselines "
                "ORDER BY inserted_at ASC, rowid ASC"
            ).fetchall()
        return [(r["agent_id"], self._decode_role(r["role"])) for r in rows]

    def delete(self, agent_id: str, role: str | None = None) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM baselines WHERE agent_id = ? AND role = ?",
                (agent_id, self._encode_role(role)),
            )
            conn.commit()
            return cur.rowcount > 0


__all__ = [
    "BaselineStore",
    "InMemoryBaselineStore",
    "FileBaselineStore",
    "SqliteBaselineStore",
]
