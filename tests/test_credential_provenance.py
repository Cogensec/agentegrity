"""Credential provenance: fingerprints of the credentials an agent uses.

A secret found leaking in a repo, a CI log or a SaaS export can be matched against
signed evidence of which agent used it. The join must work without either side
holding the other's plaintext, so the chain records an HMAC fingerprint and never
the credential. These tests lead with that privacy invariant.
"""

from __future__ import annotations

import hashlib
import json
import logging

import pytest

from agentegrity import AgentegrityClient
from agentegrity.core.credentials import (
    ENV_FINGERPRINT_KEY,
    CredentialRegistry,
    fingerprint,
)

SECRET = "sk-live-SUPERSECRETVALUE"
KEY = "org-key-alpha"


def _adapter():
    client = AgentegrityClient()
    profile = client.create_profile(name="t", agent_type="tool_using", risk_tier="low")
    return client.create_adapter("openai_agents", profile=profile)


def _drive(adapter):
    """Produce an attestation so chain evidence exists to inspect."""
    adapter._evaluate_sync("user_prompt_submit", {"prompt": "hi"})


def _credential_evidence(adapter):
    """The credential_use Evidence on the latest attestation."""
    latest = adapter.attestation_chain.records[-1]
    return [e for e in latest.evidence if e.evidence_type == "credential_use"]


# --- privacy invariants ---


def test_plaintext_never_reaches_the_chain(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    _drive(adapter)

    blob = json.dumps(adapter.attestation_chain.to_records_dict())
    assert SECRET not in blob
    assert "SUPERSECRETVALUE" not in blob


def test_plaintext_never_reaches_events_or_summary(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    _drive(adapter)

    events = json.dumps([e.to_dict() for e in adapter.events])
    assert "SUPERSECRETVALUE" not in events
    assert "SUPERSECRETVALUE" not in json.dumps(adapter.get_summary())


def test_plaintext_never_reaches_logs(monkeypatch, caplog):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    with caplog.at_level(logging.DEBUG):
        adapter = _adapter()
        adapter.declare_credential("openai", SECRET, label="openai/api_key")
        _drive(adapter)

    assert all("SUPERSECRETVALUE" not in r.getMessage() for r in caplog.records)


def test_adapter_does_not_retain_the_secret(monkeypatch):
    """Fingerprint at declare time and drop the plaintext immediately."""
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")

    assert SECRET not in repr(adapter.__dict__)


# --- fail closed ---


def test_no_key_records_nothing(monkeypatch, caplog):
    """Never downgrade to a bare hash: without a key, record nothing."""
    monkeypatch.delenv(ENV_FINGERPRINT_KEY, raising=False)
    adapter = _adapter()

    with caplog.at_level(logging.INFO, logger="agentegrity.core.credentials"):
        ref = adapter.declare_credential("openai", SECRET)

    assert ref is None
    assert adapter.credentials == ()
    assert any("fingerprint" in r.getMessage().lower() for r in caplog.records)

    _drive(adapter)
    assert "credential_use" not in json.dumps(adapter.attestation_chain.to_records_dict())


def test_fingerprint_returns_none_without_key(monkeypatch):
    monkeypatch.delenv(ENV_FINGERPRINT_KEY, raising=False)
    assert fingerprint(SECRET) is None


# --- the join ---


def test_same_secret_same_key_matches():
    """This equality is the product: the scanner side meets the agent side."""
    assert fingerprint(SECRET, key=KEY) == fingerprint(SECRET, key=KEY)


def test_different_key_does_not_match():
    """Org isolation: one tenant's fingerprints are not joinable by another."""
    assert fingerprint(SECRET, key=KEY) != fingerprint(SECRET, key="org-key-beta")


def test_different_secret_does_not_match():
    assert fingerprint(SECRET, key=KEY) != fingerprint("sk-live-OTHER", key=KEY)


def test_fingerprint_is_hmac_not_bare_hash():
    """A bare hash is globally joinable and brute-forceable for structured keys."""
    assert fingerprint(SECRET, key=KEY) != hashlib.sha256(SECRET.encode()).hexdigest()


def test_surrounding_whitespace_is_normalized():
    """A scanner reads the secret out of a repo without the file's trailing newline."""
    assert fingerprint(f"  {SECRET}\n", key=KEY) == fingerprint(SECRET, key=KEY)


# --- chain integration ---


def test_declared_credential_becomes_evidence(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    _drive(adapter)

    creds = _credential_evidence(adapter)
    assert len(creds) == 1
    assert creds[0].source == "openai"
    assert creds[0].summary == "openai/api_key"
    assert creds[0].content_hash == fingerprint(SECRET, key=KEY)


def test_evidence_summary_carries_no_sample_of_the_secret(monkeypatch):
    """No masked hints: the record is signed, chained and streamed to exporters."""
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    _drive(adapter)

    assert SECRET[-4:] not in _credential_evidence(adapter)[0].summary


def test_chain_still_verifies_with_credential_evidence(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    _drive(adapter)
    _drive(adapter)

    assert adapter.attestation_chain.verify_chain()


def test_credential_evidence_is_sticky_across_attestations(monkeypatch):
    """A leak found later still joins to every session that used the credential."""
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    _drive(adapter)
    _drive(adapter)

    for record in adapter.attestation_chain.records:
        assert "credential_use" in {e.evidence_type for e in record.evidence}


def test_duplicate_declaration_is_idempotent(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    adapter = _adapter()
    adapter.declare_credential("openai", SECRET, label="openai/api_key")
    adapter.declare_credential("openai", SECRET, label="openai/api_key")

    assert len(adapter.credentials) == 1


# --- opt-in environment sweep ---


def test_declare_from_env_is_opt_in_and_prefixed(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("UNRELATED_SETTING", "not-a-secret")

    declared = _adapter().declare_from_env(prefixes=["OPENAI_"])

    assert len(declared) == 1
    assert declared[0].label == "OPENAI_API_KEY"  # the variable name, never its value
    assert declared[0].fingerprint == fingerprint(SECRET, key=KEY)


def test_declare_from_env_records_names_not_values(monkeypatch):
    monkeypatch.setenv(ENV_FINGERPRINT_KEY, KEY)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    adapter = _adapter()
    adapter.declare_from_env(prefixes=["OPENAI_"])
    _drive(adapter)

    assert "SUPERSECRETVALUE" not in json.dumps(adapter.attestation_chain.to_records_dict())


# --- registry unit behaviour ---


def test_registry_declare_returns_none_without_key(monkeypatch):
    monkeypatch.delenv(ENV_FINGERPRINT_KEY, raising=False)
    assert CredentialRegistry().declare("openai", SECRET) is None


def test_registry_default_label_derives_from_provider():
    ref = CredentialRegistry().declare("openai", SECRET, key=KEY)
    assert ref is not None and ref.provider == "openai"
    assert "openai" in ref.label


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_empty_secret_is_ignored(empty):
    """An unset credential must not fingerprint to a value shared by every agent with it unset."""
    assert CredentialRegistry().declare("openai", empty, key=KEY) is None


def test_empty_secret_does_not_claim_the_key_is_missing(caplog):
    """The key is configured here; blaming it would send an operator hunting the wrong problem."""
    with caplog.at_level(logging.INFO, logger="agentegrity.core.credentials"):
        CredentialRegistry().declare("openai", "", key=KEY)

    assert not caplog.records
