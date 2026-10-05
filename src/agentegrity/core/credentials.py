"""Credential provenance: identify the credentials an agent uses without holding them.

When a credential leaks, a scanner can say the secret exists but not which agent used
it. This module produces the join key: a keyed, non-reversible fingerprint that both
sides compute over the same secret without either holding the other's plaintext. The
fingerprint travels in the attestation chain as ``credential_use`` Evidence; the
credential itself never does.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("agentegrity.core.credentials")

ENV_FINGERPRINT_KEY = "AGENTEGRITY_FINGERPRINT_KEY"


@dataclass(frozen=True)
class CredentialRef:
    """A credential an agent uses, identified by fingerprint and a name, never its value."""

    provider: str
    # A name such as "openai/api_key" or an env var name. Never a sample of the secret:
    # masked hints like "sk-...f3a2" would put characters of a live credential into a
    # record that is signed, chained and streamed to every exporter.
    label: str
    fingerprint: str


def _resolve_key(key: str | bytes | None) -> bytes | None:
    """Resolve the org fingerprint key from the argument or the environment."""
    if key is None:
        key = os.environ.get(ENV_FINGERPRINT_KEY)
    if not key:
        return None
    return key.encode("utf-8") if isinstance(key, str) else key


def fingerprint(secret: str, *, key: str | bytes | None = None) -> str | None:
    """HMAC-SHA256 fingerprint of a secret, or None when no key is configured."""
    # HMAC rather than a bare SHA-256: an unkeyed digest of a credential is globally
    # joinable, and structured keys (sk-, ghp_, AKIA) are cheap to brute-force offline.
    # Fail closed: with no key we record nothing instead of downgrading to a bare hash.
    resolved = _resolve_key(key)
    if resolved is None:
        return None
    # A scanner reads the secret out of a repo without the file's trailing newline.
    normalized = secret.strip()
    if not normalized:
        return None
    return hmac.new(resolved, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


class CredentialRegistry:
    """Fingerprints declared credentials and remembers the reference, never the value."""

    def __init__(self) -> None:
        self._refs: dict[str, CredentialRef] = {}
        self._warned_unconfigured = False

    def declare(self,
        provider: str,
        secret: str,
        *,
        label: str | None = None,
        key: str | bytes | None = None,
    ) -> CredentialRef | None:
        """Fingerprint a credential; None when no key is set or the secret is empty."""
        if _resolve_key(key) is None:
            self._warn_once()
            return None
        digest = fingerprint(secret, key=key)
        if digest is None:
            return None
        ref = CredentialRef(
            provider=provider,
            label=label or f"{provider}/credential",
            fingerprint=digest,
        )
        # Keyed by fingerprint, so declaring the same credential again is idempotent.
        self._refs[digest] = ref
        return ref

    def declare_from_env(self,
        prefixes: list[str] | tuple[str, ...],
        *,
        key: str | bytes | None = None,
    ) -> tuple[CredentialRef, ...]:
        """Fingerprint env vars whose names match the prefixes; the name is the label."""
        declared: list[CredentialRef] = []
        for name in sorted(os.environ):
            if not any(name.startswith(prefix) for prefix in prefixes):
                continue
            ref = self.declare(
                provider=name.split("_", 1)[0].lower(),
                secret=os.environ[name],
                label=name,
                key=key,
            )
            if ref is not None:
                declared.append(ref)
        return tuple(declared)

    def _warn_once(self) -> None:
        """Say once that fingerprints are not being recorded."""
        if self._warned_unconfigured:
            return
        self._warned_unconfigured = True
        logger.info(
            "agentegrity: no %s configured, so credential fingerprints are not recorded",
            ENV_FINGERPRINT_KEY,
        )

    @property
    def refs(self) -> tuple[CredentialRef, ...]:
        """Every credential declared so far, in declaration order."""
        return tuple(self._refs.values())
