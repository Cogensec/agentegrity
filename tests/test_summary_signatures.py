"""Session reports say whether the chain is signed, and by whom, apart from hash linkage.

Hash linkage is an unkeyed SHA-256 that anyone who edits a record can recompute,
so ``chain_hash_linked`` never stands for proof. ``chain_signatures`` reports:

* ``none``: no records yet
* ``unsigned``: no record is signed (the default without ``signing_key``)
* ``partially_signed``: some records are signed and some are not
* ``invalid``: a signed record fails verification against its own key
* ``signed``: every record verifies against the key it embeds, which anyone can produce
* ``trusted`` / ``untrusted``: every record is signed and valid, checked against trusted keys
"""

from __future__ import annotations

import asyncio

import pytest

from agentegrity import claude as claude_module
from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.core.attestation import AttestationChain
from agentegrity.core.profile import AgentProfile

crypto = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from agentegrity.core.attestation import generate_signing_key  # noqa: E402


def _public(key: object) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)  # type: ignore[attr-defined]


def _evaluated(**kwargs: object) -> ClaudeAdapter:
    adapter = ClaudeAdapter(profile=AgentProfile.default(name="t"), stream_from_env=False,
                            **kwargs)  # type: ignore[arg-type]
    asyncio.run(adapter.on_event("user_prompt_submit", {"prompt": "hi"}))
    return adapter


class TestChainSignatures:
    def test_an_empty_chain_reports_no_chain(self) -> None:
        summary = ClaudeAdapter(profile=AgentProfile.default(name="t")).get_summary()
        assert summary["chain_hash_linked"] is None
        assert summary["chain_signatures"] == "none"

    def test_the_module_report_before_any_session_reports_no_chain(self) -> None:
        claude_module.reset()
        summary = claude_module.report()
        assert summary["chain_hash_linked"] is None
        assert summary["chain_signatures"] == "none"

    def test_a_default_adapter_is_hash_linked_but_unsigned(self) -> None:
        summary = _evaluated().get_summary()
        assert summary["chain_hash_linked"] is True
        assert summary["chain_signatures"] == "unsigned"

    def test_a_signing_adapter_is_signed_under_its_embedded_key(self) -> None:
        assert _evaluated(signing_key=generate_signing_key()).get_summary()[
            "chain_signatures"] == "signed"

    def test_a_trusted_key_set_turns_signed_into_trusted_or_untrusted(self) -> None:
        key = generate_signing_key()
        adapter = _evaluated(signing_key=key)
        assert adapter.get_summary(trusted_keys={_public(key)})["chain_signatures"] == "trusted"
        other = _public(generate_signing_key())
        assert adapter.get_summary(trusted_keys={other})["chain_signatures"] == "untrusted"

    def test_trusted_keys_do_not_upgrade_an_unsigned_chain(self) -> None:
        key = generate_signing_key()
        assert _evaluated().get_summary(trusted_keys={_public(key)})[
            "chain_signatures"] == "unsigned"

    def test_a_forged_signature_is_invalid(self) -> None:
        adapter = _evaluated(signing_key=generate_signing_key())
        record = adapter.attestation_chain.records[0]
        record.signature = bytes(64)
        assert adapter.get_summary()["chain_signatures"] == "invalid"

    def test_signing_from_the_middle_of_a_session_is_partial(self) -> None:
        unsigned = _evaluated()
        resumed = ClaudeAdapter(profile=AgentProfile.default(name="t"), stream_from_env=False,
                                signing_key=generate_signing_key(),
                                chain=unsigned.attestation_chain)
        asyncio.run(resumed.on_event("user_prompt_submit", {"prompt": "again"}))
        assert resumed.get_summary()["chain_signatures"] == "partially_signed"

    def test_a_signature_without_its_key_is_invalid(self) -> None:
        adapter = _evaluated(signing_key=generate_signing_key())
        adapter.attestation_chain.records[0].public_key = None
        assert adapter.get_summary()["chain_signatures"] == "invalid"

    def test_without_the_crypto_extra_signatures_are_unverifiable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _evaluated(signing_key=generate_signing_key())
        monkeypatch.setattr("agentegrity.core.attestation._HAS_CRYPTO", False)
        assert adapter.get_summary()["chain_signatures"] == "unverifiable"
        adapter.close()

    def test_the_chain_reports_the_same_status_directly(self) -> None:
        assert AttestationChain().signature_status() == "none"
        key = generate_signing_key()
        chain = _evaluated(signing_key=key).attestation_chain
        assert chain.signature_status() == "signed"
        assert chain.signature_status({_public(key)}) == "trusted"
