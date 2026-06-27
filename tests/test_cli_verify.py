"""CLI verify-decisions tests.

Guards audit finding C3: the verifier must NOT report an unsigned or
forged chain as trustworthy. A clean exit (0) requires signatures to
verify, not merely hash linkage.
"""

from __future__ import annotations

from pathlib import Path

from agentegrity.__main__ import main
from agentegrity.core.attestation import (
    AttestationChain,
    AttestationRecord,
    Evidence,
    generate_signing_key,
)


def _record(score: float = 0.85) -> AttestationRecord:
    return AttestationRecord(
        agent_id="agent-001",
        integrity_score={"composite": score, "passed": True},
        layer_states={"adversarial": {"score": 0.9}},
        evidence=[
            Evidence(
                evidence_type="layer_result",
                source="adversarial",
                content_hash="abc123",
                summary="adversarial: 0.90 (pass)",
            )
        ],
    )


def _write_chain(tmp_path: Path, chain: AttestationChain) -> str:
    p = tmp_path / "chain.json"
    p.write_text(chain.to_json())
    return str(p)


def test_unsigned_chain_exits_nonzero(tmp_path, capsys):
    """C3: a hash-linked but unsigned chain must not pass."""
    chain = AttestationChain()
    chain.append(_record(0.9))
    chain.append(_record(0.85))
    path = _write_chain(tmp_path, chain)

    rc = main(["verify-decisions", path])
    out = capsys.readouterr().out
    assert rc == 1
    assert "signatures:     NO" in out
    # Unsigned records must read as "unsigned", never "yes".
    assert "unsigned" in out
    assert "verified" in out


def test_signed_chain_unpinned_passes_but_flags_self_vouched(tmp_path, capsys):
    key = generate_signing_key()
    chain = AttestationChain()
    r1 = _record(0.9)
    r1.sign(key)
    chain.append(r1)
    r2 = _record(0.85)
    r2.chain_previous = r1.content_hash
    r2.sign(key)
    chain.append(r2)
    path = _write_chain(tmp_path, chain)

    rc = main(["verify-decisions", path])
    out = capsys.readouterr().out
    assert rc == 0
    assert "UNPINNED" in out


def test_signed_chain_rejected_against_wrong_pinned_key(tmp_path, capsys):
    legit = generate_signing_key()
    attacker = generate_signing_key()
    # Pin the legit key; sign the chain with the attacker key.
    probe = _record()
    probe.sign(legit)
    keyfile = tmp_path / "trusted.hex"
    keyfile.write_text(probe.public_key.hex())

    chain = AttestationChain()
    r = _record(0.99)
    r.sign(attacker)
    chain.append(r)
    path = _write_chain(tmp_path, chain)

    rc = main(["verify-decisions", path, "--trusted-key", str(keyfile)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "signatures:     NO" in out
    assert "pinned" in out
