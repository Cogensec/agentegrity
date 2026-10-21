#!/usr/bin/env python3
"""Block registry publication when advertised optional frameworks have advisories.

No implicit ignore list: CrewAI and AI SDK v5 vulnerabilities are upstream,
but real consumer installs can inherit them. Bad audit output fails closed.
"""
from __future__ import annotations

import argparse
import json
import sys
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pip-audit", required=True, type=Path)
    parser.add_argument("--npm-audit", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        python = python_advisories(read_audit(args.pip_audit))
        npm = npm_advisories(read_audit(args.npm_audit))
    except ValueError as exc:
        print(f"RELEASE BLOCKED (invalid audit evidence): {exc}", file=sys.stderr)
        return 2
    for kind, findings in (("CrewAI optional Python", python), ("AI SDK v5", npm)):
        print(f"{kind}: {len(findings)} known advisory entries")
        for item in findings:
            print("  " + item)
    if python or npm:
        print("RELEASE BLOCKED: dependency findings remain; see issue #97. "
              "A green advisory CI job is not an approved risk exception.",
              file=sys.stderr)
        return 1
    print("Release dependency audit passed in both optional integration paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
