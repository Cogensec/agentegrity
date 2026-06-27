"""
Minimal CLI: ``python -m agentegrity``.

Prints version + adapter availability. Running ``python -m agentegrity
doctor`` exercises the default client end-to-end against
:meth:`AgentProfile.default` and prints the resulting composite
integrity score. This is a smoke test that takes zero reading —
if it prints a number, the install is wired correctly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from agentegrity import __version__
from agentegrity.core.attestation import AttestationChain
from agentegrity.core.decision import DecisionRecord
from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient


def _spec_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _llm_available() -> bool:
    return _spec_available("anthropic")


_ADAPTERS = [
    ("claude",         "claude_agent_sdk", "claude"),
    ("langchain",      "langchain_core",   "langchain"),
    ("openai_agents",  "agents",           "openai-agents"),
    ("crewai",         "crewai",           "crewai"),
    ("google_adk",     "google.adk",       "google-adk"),
    ("autogen",        "autogen_agentchat", "autogen"),
    ("agno",           "agno",             "agno"),
    ("bedrock_agents", "boto3",            "bedrock-agents"),
]


def _info() -> int:
    print(f"agentegrity {__version__}")
    print()
    print("Adapters:")
    for name, module, extra in _ADAPTERS:
        status = "installed" if _spec_available(module) else "not installed"
        pad = " " * max(0, 14 - len(name))
        print(f'  {name}{pad}[{status}]  — pip install "agentegrity[{extra}]"')
    print()
    print("Layers shipped: adversarial, cortical, governance, recovery")
    print()
    llm_status = "installed" if _llm_available() else "not installed"
    print(f'Optional LLM cortical checks: [{llm_status}]  — pip install "agentegrity[llm]"')
    return 0


def _doctor() -> int:
    print(f"agentegrity {__version__} — self-check")
    client = AgentegrityClient()
    profile = AgentProfile.default(name="doctor-agent")
    score = client.evaluate(profile)
    print(f"  profile:   {profile!r}")
    print(f"  composite: {score.composite:.3f}")
    print(f"  action:    {score.action}")
    print(f"  layers:    {', '.join(r.layer_name for r in score.layer_results)}")
    print("OK" if score.composite > 0 else "FAIL")
    return 0 if score.composite > 0 else 1


def _load_trusted_keys(paths: list[str]) -> set[bytes] | None:
    """Read raw Ed25519 public keys (hex, one per file) into a pinned set.

    Returns None when no anchor files are supplied, which signals the
    caller that verification is unanchored (the chain may self-vouch).
    """
    if not paths:
        return None
    keys: set[bytes] = set()
    for p in paths:
        keys.add(bytes.fromhex(Path(p).read_text().strip()))
    return keys


def _verify_decisions(path: str, trusted_key_paths: list[str]) -> int:
    """Load a chain from a JSON file and report its verification status.

    Walks ``verify_chain()`` (hash linkage), ``verify_decision_links()``,
    and ``verify_signatures()`` (cryptographic authenticity), then prints
    a per-record table. Exits non-zero on any failure.

    Hash linkage alone is NOT tamper-evidence: ``content_hash`` is an
    unkeyed SHA-256, so an attacker who edits a record can recompute the
    links and pass ``verify_chain()``. A clean exit therefore requires
    signatures to verify too. Pass ``--trusted-key`` to pin the signing
    key — without it, a chain forged with an attacker-generated key
    self-verifies.
    """
    try:
        text = Path(path).read_text()
    except OSError as exc:
        print(f"error: cannot read {path!r}: {exc}", file=sys.stderr)
        return 2

    try:
        chain = AttestationChain.from_json(text)
    except (ValueError, KeyError) as exc:
        print(f"error: cannot parse chain JSON: {exc}", file=sys.stderr)
        return 2

    try:
        trusted_keys = _load_trusted_keys(trusted_key_paths)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read trusted key: {exc}", file=sys.stderr)
        return 2

    chain_ok, broken_idx, broken_kind = chain.verify_chain_detailed()
    links_ok = chain.verify_decision_links()
    sigs_ok, sig_bad_idx = chain.verify_signatures(trusted_keys)

    print(f"agentegrity {__version__} — verify-decisions {path}")
    print(f"  records:        {len(chain)}")
    if chain_ok:
        print("  chain linkage:  yes (hash-linked)")
    else:
        print(
            f"  chain linkage:  NO (broken at index {broken_idx}, "
            f"kind={broken_kind})"
        )
    print(f"  decision links: {'yes' if links_ok else 'NO'}")
    anchor = "pinned" if trusted_keys is not None else "UNPINNED (self-vouched)"
    if sigs_ok:
        print(f"  signatures:     yes [{anchor}]")
    else:
        print(f"  signatures:     NO (record {sig_bad_idx}) [{anchor}]")
    print()
    print(
        f"  {'idx':>3}  {'kind':<12}  {'boundary/score':<22}  "
        f"{'tier':<8}  {'signed':<6}  {'verified':<8}"
    )
    for i, r in enumerate(chain.records):
        signed = "yes" if r.signature is not None else "no"
        if r.signature is None:
            verified = "unsigned"
        else:
            try:
                verified = "yes" if r.verify() else "NO"
            except ImportError:
                verified = "n/a"
        if isinstance(r, DecisionRecord):
            boundary = r.decision_point
            tier = r.capture_tier.value
        else:
            boundary = "attestation"
            tier = "-"
        print(
            f"  {i:>3}  {r.record_kind:<12}  {boundary:<22}  "
            f"{tier:<8}  {signed:<6}  {verified:<8}"
        )

    if chain_ok and links_ok and sigs_ok:
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return _info()
    if args[0] == "doctor":
        return _doctor()
    if args[0] == "verify-decisions":
        rest = args[1:]
        trusted_key_paths = []
        positional = []
        i = 0
        while i < len(rest):
            if rest[i] == "--trusted-key":
                if i + 1 >= len(rest):
                    print("error: --trusted-key requires a path", file=sys.stderr)
                    return 2
                trusted_key_paths.append(rest[i + 1])
                i += 2
            else:
                positional.append(rest[i])
                i += 1
        if not positional:
            print(
                "usage: python -m agentegrity verify-decisions "
                "[--trusted-key <pub.hex>]... <chain.json>",
                file=sys.stderr,
            )
            return 2
        return _verify_decisions(positional[0], trusted_key_paths)
    if args[0] in ("-h", "--help", "help"):
        print("usage: python -m agentegrity [doctor | verify-decisions <path>]")
        print()
        print("  (no args)                       print version + adapter availability")
        print("  doctor                          run an end-to-end self-check")
        print("  verify-decisions <chain.json>   verify a serialized chain")
        print("    --trusted-key <pub.hex>       pin a signing key (repeatable);")
        print("                                  without it, signatures are self-vouched")
        return 0
    print(f"unknown command: {args[0]!r} (try 'python -m agentegrity help')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
