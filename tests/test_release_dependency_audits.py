"""Release dependency audit gate must fail closed with realistic audit shapes."""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent.parent / "scripts" / "verify_release_dependency_audits.py"
SPEC = importlib.util.spec_from_file_location("release_audit_gate", MODULE)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def _reports(tmp_path: Path, py: object, npm: object) -> list[str]:
    p1, p2 = tmp_path / "pip.json", tmp_path / "npm.json"
    p1.write_text(json.dumps(py))
    p2.write_text(json.dumps(npm))
    return ["--pip-audit", str(p1), "--npm-audit", str(p2)]



POLICY = (
    Path(__file__).resolve().parent.parent
    / "docs" / "releases" / "v0.13.0-risk-disposition.json"
)


def _accepted_snapshot() -> tuple[
    dict[str, object], list[str], dict[str, object], dict[str, str], dict[str, object]
]:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    python = [
        f"{package}: {advisory}"
        for package, ids in policy["python"].items()
        for advisory in ids
    ]
    npm = {"vulnerabilities": {
        name: {
            "severity": metadata["severity"],
            "via": [
                {"url": f"https://github.com/advisories/{v}"} if v.startswith("GHSA-") else v
                for v in metadata["via"]
            ],
        }
        for name, metadata in policy["npm"].items()
    }}
    fixture = {"devDependencies": {"ai5": "npm:ai@5.0.272"}}
    return policy, python, npm, policy["npm_installed_versions"], fixture


def test_exact_temporary_distribution_approval() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    approved, reason = gate.verify_disposition(
        policy, python, npm, installed, fixture, "0.13.0", today=date(2026, 10, 22)
    )
    assert approved
    assert "Production exposure is NOT approved" in reason


def test_new_python_advisory_fails_closed() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    python.append("chromadb@1.5.9: PYSEC-NEW-ADVISORY")
    assert not gate.verify_disposition(
        policy, python, npm, installed, fixture, "0.13.0", today=date(2026, 10, 22)
    )[0]


def test_new_npm_vulnerability_fails_closed() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    npm["vulnerabilities"]["undici"]["via"].append(
        {"url": "https://github.com/advisories/GHSA-0000-new-0000"}
    )
    assert not gate.verify_disposition(
        policy, python, npm, installed, fixture, "0.13.0", today=date(2026, 10, 22)
    )[0]


def test_waiver_fails_when_pkg_versions_change() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    installed = dict(installed)  # do not mutate the expected policy snapshot
    installed["undici"] = "5.29.1"
    assert not gate.verify_disposition(
        policy, python, npm, installed, fixture, "0.13.0", today=date(2026, 10, 22)
    )[0]


def test_exception_cannot_extend_to_another_release() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    assert not gate.verify_disposition(
        policy, python, npm, installed, fixture, "0.13.1", today=date(2026, 10, 22)
    )[0]


def test_expired_distribution_exception_fails_closed() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    assert not gate.verify_disposition(
        policy, python, npm, installed, fixture, "0.13.0", today=date(2026, 11, 21)
    )[0]


def test_only_approved_advisory_sources_are_accepted() -> None:
    policy, python, npm, installed, fixture = _accepted_snapshot()
    npm["vulnerabilities"]["undici"]["via"].append(
        {"url": "https://untrusted.example.invalid/not-a-GHSA"}
    )
    with pytest.raises(ValueError, match="missing GitHub advisory"):
        gate.verify_disposition(
            policy, python, npm, installed, fixture, "0.13.0", today=date(2026, 10, 22)
        )


def test_no_advisories_passes(tmp_path: Path) -> None:
    args = _reports(
        tmp_path,
        {"dependencies": [{"name": "crewai", "version": "1.15.28", "vulns": []}]},
        {"vulnerabilities": {}},
    )
    assert gate.main(args) == 0


def test_skip_only_unpublished_editable_agentegrity_root(tmp_path: Path) -> None:
    args = _reports(
        tmp_path,
        {"dependencies": [
            {"name": "agentegrity", "skip_reason": "distribution marked as editable"},
            {"name": "crewai", "version": "1.15.28", "vulns": []},
        ]},
        {"vulnerabilities": {}},
    )
    assert gate.main(args) == 0


def test_other_skipped_dependencies_fail_closed(tmp_path: Path) -> None:
    args = _reports(
        tmp_path,
        {"dependencies": [
            {"name": "not-agentegrity", "skip_reason": "distribution marked as editable"},
        ]},
        {"vulnerabilities": {}},
    )
    assert gate.main(args) == 2


def test_upstream_crewai_and_v5_findings_block(tmp_path: Path) -> None:
    args = _reports(
        tmp_path,
        {"dependencies": [
            {"name": "crewai", "version": "1.15.28", "vulns": []},
            {"name": "chromadb", "version": "1.5.9", "vulns": [{"id": "PYSEC-2026-311"}]},
            {"name": "mcp", "version": "1.28.1", "vulns": [{"id": "CVE-2026-59951"}]},
        ]},
        {"vulnerabilities": {
            "@fastify/busboy": {"severity": "high"},
            "undici": {"severity": "high"},
        }},
    )
    assert gate.main(args) == 1


@pytest.mark.parametrize("py,npm", [
    ([], {"vulnerabilities": {}}),
    ({"dependencies": [{}]}, {"vulnerabilities": {}}),
    ({"dependencies": []}, []),
    ({"dependencies": []}, {"vulnerabilities": {"undici": {}}}),
])
def test_malformed_results_fail_closed(tmp_path: Path, py: object, npm: object) -> None:
    assert gate.main(_reports(tmp_path, py, npm)) == 2


def test_missing_evidence_fails_closed(tmp_path: Path) -> None:
    assert gate.main([
        "--pip-audit", str(tmp_path / "missing.json"),
        "--npm-audit", str(tmp_path / "also-missing.json"),
    ]) == 2
