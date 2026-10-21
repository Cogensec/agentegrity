"""Release dependency audit gate must fail closed with realistic audit shapes."""
from __future__ import annotations

import importlib.util
import json
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


def test_no_advisories_passes(tmp_path: Path) -> None:
    args = _reports(tmp_path, {"dependencies": [{"name": "crewai", "version": "1.15.28", "vulns": []}]}, {"vulnerabilities": {}})
    assert gate.main(args) == 0


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
