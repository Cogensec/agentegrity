"""KeyProvider Protocol + strict cross-agent link verification.

The v0.8 stub returned True whenever no peer chains were supplied,
even when the chain carried cross-agent Evidence — "unverifiable"
silently read as "verified". v0.10.0 makes the semantics strict:
cross-agent Evidence with no peer chains is a verification failure,
and a KeyProvider pins each peer record to that agent's public key.
"""

from __future__ import annotations

import importlib.util

import pytest

from agentegrity.core.attestation import (
    AttestationChain,
    Evidence,
    build_attestation_record,
)
from agentegrity.core.evaluator import (
    IntegrityScore,
    LayerResult,
    PropertyScores,
)
from agentegrity.core.keys import (
    FileKeyProvider,
    KeyProvider,
    StaticKeyProvider,
)
from agentegrity.core.profile import AgentProfile

crypto_installed = importlib.util.find_spec("cryptography") is not None


def _profile() -> AgentProfile:
    return AgentProfile.default(name="kp-test")


def _score() -> IntegrityScore:
    return IntegrityScore(
        composite=0.9,
        properties=PropertyScores(0.9, 0.9, 0.9, 0.9),
        layer_results=[
            LayerResult(
                layer_name="adversarial",
                score=0.9,
                passed=True,
                action="pass",
                details={},
            )
        ],
    )


def _chain_with_peer_evidence(peer_record) -> AttestationChain:
    chain = AttestationChain()
    record = build_attestation_record(_profile(), _score())
    record.evidence.append(
        Evidence(
            evidence_type="peer_message",
            source=f"peer-1:{peer_record.record_id}",
            content_hash=peer_record.content_hash,
            summary="peer message",
        )
    )
    chain.append(record)
    return chain


class TestProviders:
    def test_static_provider(self):
        provider = StaticKeyProvider({"agent-a": b"\x01" * 32})
        assert provider.get_public_key("agent-a") == b"\x01" * 32
        assert provider.get_public_key("agent-b") is None
        assert isinstance(provider, KeyProvider)

    def test_file_provider(self, tmp_path):
        (tmp_path / "agent-a.pub").write_text((b"\x02" * 32).hex())
        provider = FileKeyProvider(tmp_path)
        assert provider.get_public_key("agent-a") == b"\x02" * 32
        assert provider.get_public_key("missing") is None
        assert isinstance(provider, KeyProvider)

    def test_file_provider_rejects_traversal(self, tmp_path):
        provider = FileKeyProvider(tmp_path)
        with pytest.raises(ValueError):
            provider.get_public_key("../../etc/passwd")

    def test_file_provider_malformed_key_returns_none(self, tmp_path):
        (tmp_path / "agent-a.pub").write_text("not hex")
        provider = FileKeyProvider(tmp_path)
        assert provider.get_public_key("agent-a") is None


class TestStrictCrossAgentSemantics:
    def test_no_evidence_no_chains_still_true(self):
        chain = AttestationChain()
        chain.append(build_attestation_record(_profile(), _score()))
        assert chain.verify_cross_agent_links() is True

    def test_evidence_without_peer_chains_now_fails(self):
        # The v0.8 permissive stub returned True here. Unverifiable is
        # not verified.
        peer_record = build_attestation_record(_profile(), _score())
        chain = _chain_with_peer_evidence(peer_record)
        assert chain.verify_cross_agent_links() is False
        assert chain.verify_cross_agent_links(None) is False


@pytest.mark.skipif(not crypto_installed, reason="cryptography not installed")
class TestKeyPinnedVerification:
    def _signed_peer(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        key = Ed25519PrivateKey.generate()
        record = build_attestation_record(
            _profile(), _score(), signing_key=key
        )
        public_bytes = key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        return record, public_bytes

    def test_signed_peer_with_pinned_key_verifies(self):
        peer_record, public_bytes = self._signed_peer()
        peer_chain = AttestationChain()
        peer_chain.append(peer_record)
        chain = _chain_with_peer_evidence(peer_record)
        assert (
            chain.verify_cross_agent_links(
                {"peer-1": peer_chain},
                key_provider=StaticKeyProvider({"peer-1": public_bytes}),
            )
            is True
        )

    def test_wrong_pinned_key_fails(self):
        peer_record, _ = self._signed_peer()
        peer_chain = AttestationChain()
        peer_chain.append(peer_record)
        chain = _chain_with_peer_evidence(peer_record)
        assert (
            chain.verify_cross_agent_links(
                {"peer-1": peer_chain},
                key_provider=StaticKeyProvider({"peer-1": b"\x03" * 32}),
            )
            is False
        )

    def test_unsigned_peer_record_fails_under_provider(self):
        peer_record = build_attestation_record(_profile(), _score())
        peer_chain = AttestationChain()
        peer_chain.append(peer_record)
        chain = _chain_with_peer_evidence(peer_record)
        assert (
            chain.verify_cross_agent_links(
                {"peer-1": peer_chain},
                key_provider=StaticKeyProvider({"peer-1": b"\x03" * 32}),
            )
            is False
        )

    def test_missing_key_for_peer_fails(self):
        peer_record, public_bytes = self._signed_peer()
        peer_chain = AttestationChain()
        peer_chain.append(peer_record)
        chain = _chain_with_peer_evidence(peer_record)
        assert (
            chain.verify_cross_agent_links(
                {"peer-1": peer_chain},
                key_provider=StaticKeyProvider({}),
            )
            is False
        )

    def test_without_provider_signatures_not_required(self):
        # peer_chains alone keeps the v0.8 hash-resolution semantics;
        # the provider adds the signature requirement.
        peer_record = build_attestation_record(_profile(), _score())
        peer_chain = AttestationChain()
        peer_chain.append(peer_record)
        chain = _chain_with_peer_evidence(peer_record)
        assert chain.verify_cross_agent_links({"peer-1": peer_chain}) is True
