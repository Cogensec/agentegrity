"""Tests for KMSCheckpoint.

We don't talk to real AWS. Every test passes a stub KMS client that
implements just `generate_data_key` and `decrypt`. The framework's
job is to compose envelope encryption + the inner Checkpoint backend
correctly; AWS's job is to actually be a key store. Those are
separate concerns.
"""

from __future__ import annotations

import importlib.util
import json
from typing import Any

import pytest

from agentegrity.core.attestation import AttestationChain, AttestationRecord
from agentegrity.layers.checkpoint import (
    CheckpointSnapshot,
    InMemoryCheckpoint,
)

# Skip the whole module if either dependency is missing — KMSCheckpoint
# raises ImportError at construction in that case, which is the
# documented behaviour.
boto3_installed = importlib.util.find_spec("boto3") is not None
crypto_installed = importlib.util.find_spec("cryptography") is not None
pytestmark = pytest.mark.skipif(
    not (boto3_installed and crypto_installed),
    reason="KMSCheckpoint requires boto3 + cryptography "
    "(`pip install agentegrity[kms,crypto]`)",
)


def _snapshot(agent_id: str = "test-agent") -> CheckpointSnapshot:
    chain = AttestationChain()
    for i in range(2):
        chain.append(
            AttestationRecord(
                agent_id=agent_id,
                integrity_score={"composite": 0.9 - i * 0.05},
                layer_states={},
            )
        )
    return CheckpointSnapshot(
        agent_id=agent_id,
        score_history=[0.95, 0.91, 0.88],
        chain_records=chain.to_records_dict(),
        baseline={"sample_count": 12},
        metadata={"reason": "test"},
    )


class _StubKMSClient:
    """Minimal KMS client that round-trips. Generates a deterministic
    pseudo-key and the wrapped form is just `b"WRAPPED::" + plaintext`
    so we can prove the wrapper isn't smuggling the plaintext key
    elsewhere by inspecting the recorded calls."""

    def __init__(self) -> None:
        self.generate_calls: list[dict[str, Any]] = []
        self.decrypt_calls: list[dict[str, Any]] = []

    def generate_data_key(
        self,
        *,
        KeyId: str,
        KeySpec: str,
        EncryptionContext: dict[str, str] | None = None,
    ) -> dict[str, bytes]:
        self.generate_calls.append(
            {
                "KeyId": KeyId,
                "KeySpec": KeySpec,
                "EncryptionContext": dict(EncryptionContext or {}),
            }
        )
        # Deterministic 32-byte AES key — the same per call (real KMS
        # would be random); fine for tests because we just need
        # encrypt/decrypt to round-trip.
        plaintext_key = b"\x42" * 32
        wrapped = (
            b"WRAPPED::"
            + plaintext_key
            + b"::"
            + KeyId.encode("utf-8")
            + b"::"
            + json.dumps(EncryptionContext or {}, sort_keys=True).encode("utf-8")
        )
        return {"Plaintext": plaintext_key, "CiphertextBlob": wrapped}

    def decrypt(
        self,
        *,
        CiphertextBlob: bytes,
        EncryptionContext: dict[str, str] | None = None,
    ) -> dict[str, bytes]:
        self.decrypt_calls.append(
            {
                "CiphertextBlob": CiphertextBlob,
                "EncryptionContext": dict(EncryptionContext or {}),
            }
        )
        # Mirror the wrapping format from generate_data_key. Verify
        # encryption context matches what was set at wrap time —
        # real KMS does this; we want our wrapper to honour it too.
        prefix = b"WRAPPED::"
        if not CiphertextBlob.startswith(prefix):
            raise ValueError("invalid ciphertext blob")
        # Extract the original encryption context from the wrapped
        # blob to verify it matches what's being passed in.
        rest = CiphertextBlob[len(prefix) :]
        plaintext_key, _, after_key = rest.partition(b"::")
        _key_id, _, original_ctx_bytes = after_key.partition(b"::")
        original_ctx = json.loads(original_ctx_bytes.decode("utf-8"))
        if dict(EncryptionContext or {}) != original_ctx:
            raise RuntimeError(
                "InvalidCiphertextException: encryption context mismatch"
            )
        return {"Plaintext": plaintext_key}


@pytest.fixture
def kms_client() -> _StubKMSClient:
    return _StubKMSClient()


@pytest.fixture
def inner_backend() -> InMemoryCheckpoint:
    return InMemoryCheckpoint()


@pytest.fixture
def kms_checkpoint(kms_client, inner_backend):
    from agentegrity.layers.kms_checkpoint import KMSCheckpoint

    return KMSCheckpoint(
        inner=inner_backend,
        key_id="alias/test-agentegrity",
        kms_client=kms_client,
    )


# ----- Construction guards -------------------------------------------------


class TestConstruction:
    def test_empty_key_id_rejected(self, kms_client, inner_backend):
        from agentegrity.layers.kms_checkpoint import KMSCheckpoint

        with pytest.raises(ValueError, match="key_id"):
            KMSCheckpoint(
                inner=inner_backend, key_id="", kms_client=kms_client
            )

    def test_uses_default_region_from_env(
        self, monkeypatch, inner_backend, kms_client
    ):
        # When a kms_client is supplied directly, the env var path
        # isn't exercised. This is a sanity check that supplying a
        # client takes precedence over region resolution.
        from agentegrity.layers.kms_checkpoint import KMSCheckpoint

        monkeypatch.setenv("AWS_REGION", "us-fake-1")
        ckpt = KMSCheckpoint(
            inner=inner_backend, key_id="alias/x", kms_client=kms_client
        )
        # No AWS calls happened; the supplied client is held verbatim.
        assert ckpt._kms is kms_client


# ----- Round-trip ----------------------------------------------------------


class TestRoundTrip:
    def test_save_then_load_returns_equivalent_snapshot(
        self, kms_checkpoint, kms_client, inner_backend
    ):
        original = _snapshot()
        cid = kms_checkpoint.save(original)
        # KMS was hit exactly once on save.
        assert len(kms_client.generate_calls) == 1
        # The inner backend stored an envelope, not plaintext.
        stored = inner_backend.load(cid)
        assert stored is not None
        assert stored.score_history == []
        assert stored.chain_records == []
        assert stored.baseline is None
        env = stored.metadata.get("agentegrity_kms_envelope")
        assert isinstance(env, dict)
        assert env["key_id"] == "alias/test-agentegrity"
        assert env["version"] == 1

        loaded = kms_checkpoint.load(cid)
        assert len(kms_client.decrypt_calls) == 1
        assert loaded is not None
        assert loaded.agent_id == original.agent_id
        assert loaded.score_history == original.score_history
        assert loaded.chain_records == original.chain_records
        assert loaded.baseline == original.baseline
        assert loaded.metadata == original.metadata

    def test_chain_round_trips_to_verifiable_form(self, kms_checkpoint):
        original = _snapshot()
        cid = kms_checkpoint.save(original)
        loaded = kms_checkpoint.load(cid)
        assert loaded is not None
        chain = AttestationChain.from_dict_list(loaded.chain_records)
        assert chain.verify_chain()

    def test_load_unknown_id_returns_none(self, kms_checkpoint):
        assert kms_checkpoint.load("nope") is None

    def test_list_ids_passthrough(self, kms_checkpoint, inner_backend):
        kms_checkpoint.save(_snapshot(agent_id="a"))
        kms_checkpoint.save(_snapshot(agent_id="b"))
        ids = kms_checkpoint.list_ids()
        # Order matches the inner backend's ordering.
        assert ids == inner_backend.list_ids()
        assert len(ids) == 2

    def test_latest_returns_decrypted_snapshot(self, kms_checkpoint):
        kms_checkpoint.save(_snapshot(agent_id="first"))
        kms_checkpoint.save(_snapshot(agent_id="second"))
        latest = kms_checkpoint.latest()
        assert latest is not None
        assert latest.agent_id == "second"


# ----- Encryption context binding -----------------------------------------


class TestEncryptionContext:
    def test_context_round_trips(self, kms_client, inner_backend):
        from agentegrity.layers.kms_checkpoint import KMSCheckpoint

        ckpt = KMSCheckpoint(
            inner=inner_backend,
            key_id="alias/x",
            encryption_context={"agent_id": "a1", "tenant": "t1"},
            kms_client=kms_client,
        )
        cid = ckpt.save(_snapshot())
        loaded = ckpt.load(cid)
        assert loaded is not None
        # The encryption context was passed to KMS on both calls.
        assert kms_client.generate_calls[0]["EncryptionContext"] == {
            "agent_id": "a1",
            "tenant": "t1",
        }
        assert kms_client.decrypt_calls[0]["EncryptionContext"] == {
            "agent_id": "a1",
            "tenant": "t1",
        }

    def test_context_mismatch_at_decrypt_fails(
        self, kms_client, inner_backend
    ):
        # Save with one context, swap the wrapper to a different
        # context, attempt to load — must fail.
        from agentegrity.layers.kms_checkpoint import KMSCheckpoint

        wrap = KMSCheckpoint(
            inner=inner_backend,
            key_id="alias/x",
            encryption_context={"tenant": "t1"},
            kms_client=kms_client,
        )
        cid = wrap.save(_snapshot())

        attacker_view = KMSCheckpoint(
            inner=inner_backend,
            key_id="alias/x",
            encryption_context={"tenant": "t-malicious"},
            kms_client=kms_client,
        )
        with pytest.raises(RuntimeError, match="encryption context"):
            attacker_view.load(cid)


# ----- Tampering -----------------------------------------------------------


class TestTamperResistance:
    def test_modified_ciphertext_fails_to_decrypt(
        self, kms_checkpoint, inner_backend, kms_client
    ):
        cid = kms_checkpoint.save(_snapshot())
        # Reach into the inner backend, mutate one byte of the
        # ciphertext, attempt to load.
        stored = inner_backend.load(cid)
        env = stored.metadata["agentegrity_kms_envelope"]
        ct = bytes.fromhex(env["ciphertext"])
        mutated = bytearray(ct)
        mutated[0] ^= 0xFF
        env["ciphertext"] = bytes(mutated).hex()
        # Re-save the inner backend (InMemory just overwrites).
        inner_backend.save(stored)

        # AESGCM auth-tag verification must fail.
        with pytest.raises(Exception):  # cryptography raises InvalidTag
            kms_checkpoint.load(cid)

    def test_missing_envelope_metadata_raises(
        self, kms_checkpoint, inner_backend
    ):
        # A snapshot without the envelope (e.g. one written by a
        # non-KMS backend pointing at the same inner storage) must
        # not silently appear empty — load must raise.
        bare = _snapshot(agent_id="bare")
        inner_backend.save(bare)  # bypasses the KMS wrap entirely
        with pytest.raises(ValueError, match="agentegrity_kms_envelope"):
            kms_checkpoint.load(bare.checkpoint_id)


# ----- Plaintext leakage ---------------------------------------------------


class TestNoPlaintextLeak:
    def test_inner_backend_never_sees_plaintext(
        self, kms_checkpoint, inner_backend, kms_client
    ):
        original = _snapshot()
        cid = kms_checkpoint.save(original)
        stored = inner_backend.load(cid)
        # The serialised stored snapshot should NOT contain the
        # plaintext score_history values, the agent's chain records,
        # or the plaintext baseline.
        stored_json = json.dumps(stored.to_dict())
        for v in original.score_history:
            assert str(v) not in stored_json
        for record in original.chain_records:
            assert record["record_id"] not in stored_json
        # Metadata IS allowed to include the envelope — that's the
        # ciphertext + wrapped key, not plaintext.
        assert "agentegrity_kms_envelope" in stored_json

    def test_envelope_never_has_a_separate_plaintext_key_field(
        self, kms_checkpoint, inner_backend
    ):
        # Real KMS would return a CiphertextBlob that doesn't contain
        # the plaintext key — that's KMS's job. The framework's job is
        # to never PERSIST a separate plaintext key field anywhere on
        # the envelope. Confirm the envelope's only key-bearing field
        # is wrapped_data_key.
        cid = kms_checkpoint.save(_snapshot())
        stored = inner_backend.load(cid)
        env = stored.metadata["agentegrity_kms_envelope"]
        # Documented envelope keys — anything else would be a leak.
        assert set(env.keys()) == {
            "version",
            "key_id",
            "wrapped_data_key",
            "nonce",
            "ciphertext",
            "encryption_context",
        }
        # The wrapped_data_key bears the wrapping prefix.
        assert env["wrapped_data_key"].startswith(b"WRAPPED::".hex())
