#!/usr/bin/env python3
"""
Assert that every place we declare a version agrees with the canonical
version in pyproject.toml.

Checks:
  - pyproject.toml                           [project].version
  - src/agentegrity/__init__.py              __version__
  - README.md                                "library-vX.Y.Z" shields badge
  - README.md "vX.Y.Z ships" / "vX.Y.Z — ..." current-release prose

Mirrors clients/typescript/scripts/check-versions.ts which covers the
@agentegrity/* npm packages.

Exit code 0 on parity, 1 on drift, 2 on missing canonical version.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def fail(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def canonical_version() -> str:
    text = read(ROOT / "pyproject.toml")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        print("could not find version in pyproject.toml", file=sys.stderr)
        sys.exit(2)
    return m.group(1)


def check_init(version: str) -> bool:
    path = ROOT / "src" / "agentegrity" / "__init__.py"
    text = read(path)
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        fail(f"{path.relative_to(ROOT)} has no __version__")
        return False
    if m.group(1) != version:
        fail(f"{path.relative_to(ROOT)} __version__={m.group(1)} != pyproject {version}")
        return False
    ok(f"src/agentegrity/__init__.py __version__={version}")
    return True


def check_readme_badge(version: str) -> bool:
    path = ROOT / "README.md"
    text = read(path)
    m = re.search(r"library-v([0-9]+\.[0-9]+\.[0-9]+)-", text)
    if not m:
        fail("README.md library-vX.Y.Z badge not found")
        return False
    if m.group(1) != version:
        fail(f"README.md badge v{m.group(1)} != pyproject {version}")
        return False
    ok(f"README.md badge v{version}")
    return True


def check_readme_prose(version: str) -> bool:
    """Flag 'vX.Y.Z (current)' and 'vX.Y.Z ships' tokens in README that
    disagree with the canonical version. Roadmap bullets formatted as
    `**vA.B.C — description**` are tolerated since they are historical /
    forward-looking entries, not present-tense claims about the shipped lib.
    The shields badge is checked separately by check_readme_badge."""
    path = ROOT / "README.md"
    text = read(path)
    bad: list[str] = []
    # Pattern 1: "vX.Y.Z ships ..." — a present-tense claim about the lib.
    for m in re.finditer(r"\bv(\d+\.\d+\.\d+)\s+ships\b", text):
        if m.group(1) != version:
            bad.append(f"  '{m.group(0)}' should be 'v{version} ships'")
    # Pattern 2: "(current)" tag on a roadmap bullet.
    for m in re.finditer(
        r"\*\*v(\d+\.\d+\.\d+)\s+—\s+[^*]*\(current\)", text
    ):
        if m.group(1) != version:
            bad.append(f"  roadmap '(current)' tag is on v{m.group(1)}, not v{version}")
    # Pattern 3: "As of vX.Y.Z the library ships ..." — present-tense claim.
    for m in re.finditer(r"As of v(\d+\.\d+\.\d+)\b", text):
        if m.group(1) != version:
            bad.append(f"  'As of v{m.group(1)}' should be 'As of v{version}'")
    if bad:
        fail(f"README.md present-tense claims disagree with v{version}:")
        for b in bad:
            print(b, file=sys.stderr)
        return False
    ok(f"README.md prose 'ships'/'(current)'/'As of' references match v{version}")
    return True


def main() -> int:
    version = canonical_version()
    print(f"canonical version: {version}\n")
    checks = [
        check_init(version),
        check_readme_badge(version),
        check_readme_prose(version),
    ]
    if not all(checks):
        print("\nVersion drift detected. Bump every site together.", file=sys.stderr)
        return 1
    print(f"\nAll Python sites match pyproject version {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
