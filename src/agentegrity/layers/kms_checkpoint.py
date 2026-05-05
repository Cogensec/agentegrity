"""KMS-encrypted Checkpoint wrapper.

Closes the residual-risk line in `spec/threat-model.md` T-T2: an
attacker with filesystem write access can mutate the JSON contents
of a `FileCheckpoint`. Envelope encryption with a KMS-managed CMK
takes the at-rest secrecy story off the local disk and into a
cloud-managed key.

Architecture
------------

This module provides :class:`KMSCheckpoint`, a *wrapper* around any
inner :class:`Checkpoint` (typically a :class:`FileCheckpoint` or
:class:`SqliteCheckpoint`). On save, the wrapper:

1. Generates a fresh AES-256 data key from KMS via
   ``GenerateDataKey``.
2. Encrypts the snapshot's serialised JSON with the plaintext data
   key (AES-GCM via ``cryptography``).
3. Persists the *ciphertext* + the *KMS-wrapped data key* + the GCM
   IV + auth tag through the inner Checkpoint, repackaged as a
   :class:`CheckpointSnapshot` whose ``metadata`` carries the
   envelope.
4. Discards the plaintext data key.

On load, the wrapper retrieves the snapshot, calls KMS ``Decrypt``
on the wrapped data key, decrypts the ciphertext, and returns the
original :class:`CheckpointSnapshot`.

Reads and writes still flow through whichever inner backend the
operator chose, so the durability + atomic-write guarantees of
:class:`FileCheckpoint` and the indexed-lookup guarantees of
:class:`SqliteCheckpoint` are preserved end-to-end.

Operational story
-----------------

* IAM principal MUST have ``kms:GenerateDataKey`` and ``kms:Decrypt``
  on the configured key id. Restricting these per-environment is the
  primary access-control story.
* Key rotation is automatic on the AWS side — every fresh
  ``GenerateDataKey`` uses the current CMK version, and old wrapped
  data keys remain decryptable by KMS until the CMK is scheduled for
  deletion. The framework requires no migration on rotation.
* Audit: every KMS call is logged in CloudTrail, so a tamper attempt
  on the inner Checkpoint that triggers a decrypt failure has a
  parallel audit trail in CloudTrail you can correlate against.

Behind ``pip install agentegrity[kms]`` (boto3 + cryptography). When
the extra isn't installed, importing this module raises
``ImportError`` with the install hint — the operator must
deliberately opt in.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from agentegrity.layers.checkpoint import (
    Checkpoint,
    CheckpointSnapshot,
)

logger = logging.getLogger("agentegrity.layers.kms_checkpoint")

if TYPE_CHECKING:
    pass

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

try:
    import boto3  # type: ignore[import-untyped]
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False


# Wire-format constants. Bumping these is a forward-compat break and
# requires a migration story.
_ENVELOPE_VERSION = 1
_AES_KEY_BYTES = 32  # AES-256
_GCM_NONCE_BYTES = 12


class KMSCheckpoint:
    """Envelope-encrypts every snapshot before delegating to an inner
    :class:`Checkpoint`.

    Parameters
    ----------
    inner : Checkpoint
        The actual storage backend (FileCheckpoint, SqliteCheckpoint,
        or any third-party impl). Receives the encrypted payload.
    key_id : str
        AWS KMS key identifier — accepts a key ID
        (``"abc123-..."``), an alias (``"alias/agentegrity-prod"``),
        or a full ARN. Passed through to KMS ``GenerateDataKey``
        / ``Decrypt`` unchanged.
    region_name : str, optional
        AWS region. Falls back to ``AWS_REGION`` / ``AWS_DEFAULT_REGION``
        if omitted, then to boto3's default config-file resolution.
    encryption_context : dict[str, str], optional
        KMS encryption context — included in the wrapped data key
        and verified on decrypt. Use to bind the snapshot to its
        intended scope (e.g. ``{"agent_id": "production-bot",
        "tenant": "acme"}``). A different encryption context at
        decrypt time = the operation fails. None = no context (KMS
        accepts this).
    kms_client : boto3 KMS client, optional
        Pre-built client. Useful for testing and for operators with
        custom retry policies / endpoint URLs. None = build via
        ``boto3.client("kms", region_name=...)``.

    Raises
    ------
    ImportError
        When ``boto3`` or ``cryptography`` is not installed.
        Install via ``pip install agentegrity[kms,crypto]``.
    """

    def __init__(
        self,
        inner: Checkpoint,
        key_id: str,
        *,
        region_name: str | None = None,
        encryption_context: dict[str, str] | None = None,
        kms_client: Any | None = None,
    ) -> None:
        if not _HAS_BOTO3:
            raise ImportError(
                "KMSCheckpoint requires boto3. "
                "Install with: pip install agentegrity[kms]"
            )
        if not _HAS_CRYPTO:
            raise ImportError(
                "KMSCheckpoint requires cryptography. "
                "Install with: pip install agentegrity[crypto]"
            )
        if not key_id:
            raise ValueError("KMSCheckpoint requires a non-empty key_id")
        self._inner = inner
        self._key_id = key_id
        self._encryption_context = dict(encryption_context or {})
        if kms_client is None:
            region = (
                region_name
                or os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
            )
            self._kms = boto3.client("kms", region_name=region)
        else:
            self._kms = kms_client

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _encrypt(self, snapshot: CheckpointSnapshot) -> CheckpointSnapshot:
        """Take a plaintext snapshot, return a snapshot whose payload
        sections are encrypted and whose metadata carries the envelope."""
        plaintext = json.dumps(snapshot.to_dict(), sort_keys=True).encode("utf-8")

        kms_response = self._kms.generate_data_key(
            KeyId=self._key_id,
            KeySpec="AES_256",
            EncryptionContext=self._encryption_context or {},
        )
        plaintext_key = kms_response["Plaintext"]
        wrapped_key = kms_response["CiphertextBlob"]

        try:
            cipher = AESGCM(plaintext_key)
            nonce = os.urandom(_GCM_NONCE_BYTES)
            ciphertext = cipher.encrypt(nonce, plaintext, None)
        finally:
            # Best-effort wipe of the plaintext key from process
            # memory. Python's GC doesn't guarantee this but it's
            # better than letting the bytes linger in a held variable.
            del plaintext_key

        envelope = {
            "version": _ENVELOPE_VERSION,
            "key_id": self._key_id,
            "wrapped_data_key": wrapped_key.hex(),
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
            "encryption_context": self._encryption_context,
        }

        # Build a snapshot whose CONTENTS are deliberately empty (no
        # plaintext leaks via to_dict()) and whose metadata carries
        # the envelope. The inner backend stores this verbatim.
        return CheckpointSnapshot(
            agent_id=snapshot.agent_id,
            checkpoint_id=snapshot.checkpoint_id,
            created_at=snapshot.created_at,
            score_history=[],
            chain_records=[],
            baseline=None,
            metadata={"agentegrity_kms_envelope": envelope},
        )

    def _decrypt(
        self, encrypted_snap: CheckpointSnapshot
    ) -> CheckpointSnapshot:
        """Reverse of _encrypt — pull the envelope out of metadata,
        unwrap the data key via KMS, decrypt the payload, return the
        original snapshot."""
        envelope = encrypted_snap.metadata.get("agentegrity_kms_envelope")
        if not isinstance(envelope, dict):
            raise ValueError(
                f"checkpoint {encrypted_snap.checkpoint_id!r} has no "
                "agentegrity_kms_envelope metadata — was it written by a "
                "different backend?"
            )
        if envelope.get("version") != _ENVELOPE_VERSION:
            raise ValueError(
                f"unsupported envelope version: {envelope.get('version')!r}"
            )

        wrapped_key = bytes.fromhex(envelope["wrapped_data_key"])
        nonce = bytes.fromhex(envelope["nonce"])
        ciphertext = bytes.fromhex(envelope["ciphertext"])

        # Pass the WRAPPER's encryption context — not the envelope's —
        # so the binding works the way KMS intends. If an attacker
        # tampered with the stored envelope to swap the context, KMS
        # would still verify against the wrapper's context. If the
        # wrapper instance's context differs from what was set at
        # wrap time, KMS refuses with InvalidCiphertextException.
        decrypt_response = self._kms.decrypt(
            CiphertextBlob=wrapped_key,
            EncryptionContext=self._encryption_context or {},
        )
        plaintext_key = decrypt_response["Plaintext"]

        try:
            cipher = AESGCM(plaintext_key)
            plaintext_bytes = cipher.decrypt(nonce, ciphertext, None)
        finally:
            del plaintext_key

        plaintext_dict = json.loads(plaintext_bytes.decode("utf-8"))
        return CheckpointSnapshot.from_dict(plaintext_dict)

    # ------------------------------------------------------------------
    # Checkpoint Protocol surface — delegates to inner backend with
    # transparent envelope encryption / decryption.
    # ------------------------------------------------------------------

    def save(self, snapshot: CheckpointSnapshot) -> str:
        encrypted = self._encrypt(snapshot)
        return self._inner.save(encrypted)

    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        encrypted = self._inner.load(checkpoint_id)
        if encrypted is None:
            return None
        return self._decrypt(encrypted)

    def list_ids(self) -> list[str]:
        return self._inner.list_ids()

    def latest(self) -> CheckpointSnapshot | None:
        encrypted = self._inner.latest()
        if encrypted is None:
            return None
        return self._decrypt(encrypted)


__all__ = ["KMSCheckpoint"]
