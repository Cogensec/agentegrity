#!/usr/bin/env python3
"""Fail closed on optional-framework advisories, except an exact dated disposition.

The only supported exception is a short-lived v0.13.0 distribution-only risk
acceptance. It does not accept vulnerable production deployments. Every package,
version, advisory and npm transitive relationship must match the approved
snapshot; new findings, missing evidence, changed versions and expired approvals
still block publication.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def read_audit(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid or missing audit report {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"expected object in {path}")
    return result


def python_advisories(report: dict[str, Any]) -> list[str]:
    entries = report.get("dependencies")
    if not isinstance(entries, list):
        raise ValueError("pip-audit: missing dependencies array")
    out: list[str] = []
    for package in entries:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise ValueError("pip-audit: malformed dependency")
        # pip-audit --skip-editable lists the unpublished local root this way.
        # Every other skipped dependency is an evidence failure.
        if package.get("skip_reason") is not None:
            if (
                package["name"].lower() == "agentegrity"
                and package["skip_reason"] == "distribution marked as editable"
            ):
                continue
            raise ValueError(f"pip-audit: unsupported skipped dependency {package['name']}")
        advisories = package.get("vulns")
        if not isinstance(advisories, list):
            raise ValueError("pip-audit: missing vulns array")
        for v in advisories:
            if not isinstance(v, dict) or not isinstance(v.get("id"), str):
                raise ValueError("pip-audit: malformed advisory")
            out.append(f"{package['name']}@{package.get('version', '?')}: {v['id']}")
    return out


def npm_advisories(report: dict[str, Any]) -> list[str]:
    entries = report.get("vulnerabilities")
    if not isinstance(entries, dict):
        raise ValueError("npm audit: missing vulnerabilities map")
    out: list[str] = []
    for name, vuln in sorted(entries.items()):
        if not isinstance(vuln, dict) or not isinstance(vuln.get("severity"), str):
            raise ValueError(f"npm audit: malformed entry {name}")
        out.append(f"{name}: {vuln['severity']}")
    return out


def npm_snapshot(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        raise ValueError("npm audit: missing vulnerabilities map")
    snapshot: dict[str, dict[str, Any]] = {}
    for name, vulnerability in vulnerabilities.items():
        if not isinstance(name, str) or not isinstance(vulnerability, dict):
            raise ValueError("npm audit: invalid vulnerability")
        severity, via = vulnerability.get("severity"), vulnerability.get("via")
        if severity not in {"low", "moderate", "high", "critical"} or not isinstance(via, list):
            raise ValueError(f"npm audit: missing severity/via for {name}")
        identities: list[str] = []
        for reference in via:
            if isinstance(reference, str) and reference:
                identities.append(reference)
            elif isinstance(reference, dict):
                url = reference.get("url")
                if (
                    not isinstance(url, str)
                    or not url.startswith("https://github.com/advisories/GHSA-")
                ):
                    raise ValueError(f"npm audit: missing GitHub advisory identifier for {name}")
                identities.append(url.rsplit("/", 1)[-1])
            else:
                raise ValueError(f"npm audit: invalid nested advisory for {name}")
        if len(identities) != len(set(identities)) or not identities:
            raise ValueError(f"npm audit: duplicate or missing advisory identities for {name}")
        snapshot[name] = {"severity": severity, "via": sorted(identities)}
    return snapshot


def verify_disposition(
    policy: dict[str, Any],
    python_findings: list[str],
    npm_report: dict[str, Any],
    installed: dict[str, Any],
    fixture_manifest: dict[str, Any],
    release_version: str,
    today: date | None = None,
) -> tuple[bool, str]:
    if policy.get("schema_version") != 1 or policy.get("release_version") != "0.13.0":
        return False, "unknown risk policy schema or release"
    if release_version != "0.13.0":
        return False, "policy cannot apply to another release"
    if policy.get("id") != "AGENTEGRITY-013-OPTIONAL-DISTRIBUTION-20261021":
        return False, "unexpected disposition identifier"
    scope = policy.get("scope")
    if not isinstance(scope, str) or not scope.startswith(
        "OSS package distribution of Agentegrity v0.13.0 only;"
    ):
        return False, "production deployment is outside approved distribution-only scope"
    try:
        approved = date.fromisoformat(policy["approved_on"])
        expires = date.fromisoformat(policy["expires_on"])
    except (ValueError, KeyError, TypeError):
        return False, "invalid approval or expiration date"
    current = today or date.today()
    if (
        current < approved
        or current > expires
        or expires <= approved
        or expires - approved > timedelta(days=31)
    ):
        return False, "disposition not active or too broad in time"
    if not isinstance(policy.get("approval_basis"), str) or not policy["approval_basis"]:
        return False, "no documented approval"
    if not isinstance(policy.get("mandatory_controls"), list) or len(
        policy["mandatory_controls"]
    ) < 6:
        return False, "missing mandatory deployment restrictions"
    allowed_python = policy.get("python")
    if not isinstance(allowed_python, dict):
        return False, "missing Python advisory allowlist"
    expected_python: list[str] = []
    for package, advisory_ids in allowed_python.items():
        if not isinstance(package, str) or not isinstance(advisory_ids, list):
            return False, "malformed Python exception"
        expected_python.extend(f"{package}: {advisory}" for advisory in advisory_ids)
    if sorted(python_findings) != sorted(expected_python):
        return False, "Python advisory IDs or package versions changed"
    if npm_snapshot(npm_report) != policy.get("npm"):
        return False, "npm packages, severities, transitive paths or advisory IDs changed"
    expected_versions = policy.get("npm_installed_versions")
    if not isinstance(expected_versions, dict) or installed != expected_versions:
        return False, "installed legacy npm package versions changed"
    if fixture_manifest.get("devDependencies", {}).get("ai5") != "npm:ai@5.0.272":
        return False, "legacy fixture is no longer pinned to the accepted AI SDK v5"
    return True, (
        f"Conditionally accepted for v0.13.0 OSS distribution ONLY; "
        f"expires {expires.isoformat()}. Production exposure is NOT approved."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pip-audit", required=True, type=Path)
    parser.add_argument("--npm-audit", required=True, type=Path)
    parser.add_argument("--disposition-policy", type=Path)
    parser.add_argument("--npm-installed", type=Path)
    parser.add_argument("--fixture-manifest", type=Path)
    parser.add_argument("--release-version", type=str)
    args = parser.parse_args(argv)
    try:
        python = python_advisories(read_audit(args.pip_audit))
        npm_report = read_audit(args.npm_audit)
        npm = npm_advisories(npm_report)
        # Strict per-advisory matching is required when claiming an approved
        # exception. Without one, any reported package is blocked outright.
        print(f"CrewAI optional Python: {len(python)} advisory entries")
        print(f"Legacy AI SDK v5: {len(npm)} affected npm packages")
        for item in python + npm:
            print("  " + item)
        if not python and not npm:
            print("Release dependency audit CLEAR; no risk exception needed.")
            return 0
        if args.disposition_policy is None:
            print("RELEASE BLOCKED: unresolved optional dependency advisories", file=sys.stderr)
            return 1
        if not (args.npm_installed and args.fixture_manifest and args.release_version):
            raise ValueError("conditional disposition requires version and installed npm evidence")
        # Compare the actual release tag to the version being built, not just
        # the static exception file. Tag workflow uses Python 3.12.
        import tomllib

        with Path("pyproject.toml").open("rb") as metadata:
            built_version = tomllib.load(metadata)["project"]["version"]
        if built_version != args.release_version:
            raise ValueError("release tag does not match source distribution version")
        ok, reason = verify_disposition(
            read_audit(args.disposition_policy),
            python,
            npm_report,
            read_audit(args.npm_installed),
            read_audit(args.fixture_manifest),
            args.release_version,
        )
    except ValueError as exc:
        print(f"RELEASE BLOCKED (invalid audit evidence): {exc}", file=sys.stderr)
        return 2
    if not ok:
        print(f"RELEASE BLOCKED (outside approved exception): {reason}", file=sys.stderr)
        return 1
    print("RELEASE SECURITY: CONDITIONAL DISTRIBUTION-ONLY DISPOSITION")
    print(reason)
    print("NOT an approval of deployed vulnerable upstream frameworks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
