"""KeyProvider Protocol — resolve agent identities to public keys.

Cross-agent verification needs a trust anchor: without one, a forged
peer chain signed with an attacker-generated key self-verifies, since
every record embeds its own public key. A :class:`KeyProvider` maps
``agent_id`` to the raw Ed25519 public key (32 bytes) that agent is
*supposed* to sign with, and
:meth:`AttestationChain.verify_cross_agent_links` rejects any peer
record whose embedded key differs or whose signature fails.

Reference implementations: :class:`StaticKeyProvider` (in-memory
mapping) and :class:`FileKeyProvider` (``<agent_id>.pub`` hex files,
the same format the CLI's ``--trusted-key`` reads). Env- and
KMS-backed providers are deliberate follow-ups; the Protocol is the
stable seam.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from agentegrity.layers.checkpoint import validate_storage_identifier

logger = logging.getLogger("agentegrity.keys")


@runtime_checkable
class KeyProvider(Protocol):
    """Resolves an agent identity to its pinned raw public key."""

    def get_public_key(self, agent_id: str) -> bytes | None:
        """Return the agent's raw Ed25519 public key (32 bytes), or
        None when the agent is unknown."""
        ...


class StaticKeyProvider:
    """KeyProvider over an in-memory ``{agent_id: raw_key}`` mapping."""

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        self._keys = dict(keys)

    def get_public_key(self, agent_id: str) -> bytes | None:
        return self._keys.get(agent_id)

    def __repr__(self) -> str:
        return f"StaticKeyProvider(agents={len(self._keys)})"


class FileKeyProvider:
    """KeyProvider reading ``<agent_id>.pub`` files (hex) from a directory.

    File format matches the CLI's ``--trusted-key`` anchors: one raw
    Ed25519 public key, hex-encoded. A malformed file resolves to None
    (logged) rather than raising, so one bad key file cannot take down
    verification of every other agent.
    """

    def __init__(self, directory: str | Path) -> None:
        self._root = Path(directory)

    def get_public_key(self, agent_id: str) -> bytes | None:
        validate_storage_identifier(agent_id, kind="agent_id")
        path = self._root / f"{agent_id}.pub"
        if not path.is_file():
            return None
        try:
            return bytes.fromhex(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            logger.warning("unreadable key file %s: %s", path, exc)
            return None

    def __repr__(self) -> str:
        return f"FileKeyProvider(root={str(self._root)!r})"


__all__ = ["FileKeyProvider", "KeyProvider", "StaticKeyProvider"]
