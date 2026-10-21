#!/usr/bin/env python3
"""Inspect v0.13.0 candidate wheel/sdist before any registry publication.

This intentionally uses only Python's stdlib so it can run in the build job.
It does not substitute for a clean-environment install or tests.
"""

from __future__ import annotations

import argparse
import email
import re
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
VERSION = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(), re.MULTILINE)
if VERSION is None:
    raise SystemExit("Missing [project].version in pyproject.toml")
version = VERSION.group(1)
dist = ROOT / "dist"

def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"Release artifact check failed: {message}")

def validate_paths(paths: list[str], archive: str) -> None:
    require(all(not p.startswith("/") and ".." not in Path(p).parts for p in paths),
            f"{archive}: unsafe archive path")
    # Never ship development credentials or repository-local environment files.
    secret = re.compile(r"(^|/)(\.env(\.|$)|id_rsa($|\.)|[^/]+\.(pem|p12|key)$)", re.I)
    require(not any(secret.search(p) for p in paths),
            f"{archive}: a likely credential/private config is packaged")

def validate_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        validate_paths(names, path.name)
        for needed in (
            "agentegrity/__init__.py",
            "agentegrity/__main__.py",
            "agentegrity/core/action_security.py",
            "agentegrity/core/trust_boundaries.py",
            "agentegrity/hooks/protocol.py",
        ):
            require(needed in names, f"{path.name}: missing {needed}")
        metadata_files = [p for p in names if p.endswith(".dist-info/METADATA")]
        require(len(metadata_files) == 1, f"{path.name}: expected one wheel metadata file")
        msg = email.message_from_bytes(archive.read(metadata_files[0]))
        require(msg.get("Name", "").lower() == "agentegrity", "wheel project name mismatch")
        require(msg.get("Version") == version, f"wheel version != {version}")
        require(msg.get("Requires-Python") is not None, "wheel missing Python compatibility")

def validate_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        validate_paths(names, path.name)
        root = f"agentegrity-{version}/"
        for needed in (
            "pyproject.toml",
            "README.md",
            "LICENSE",
            "src/agentegrity/__init__.py",
            "src/agentegrity/core/action_security.py",
            "src/agentegrity/core/trust_boundaries.py",
        ):
            require(root + needed in names, f"{path.name}: missing {needed}")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default=str(dist))
    args = parser.parse_args()
    folder = Path(args.directory)
    wheels = list(folder.glob(f"agentegrity-{version}-*.whl"))
    sdists = list(folder.glob(f"agentegrity-{version}.tar.gz"))
    require(len(wheels) == 1 and len(sdists) == 1,
            f"expected one wheel and one sdist for {version}; got {len(wheels)} + {len(sdists)}")
    validate_wheel(wheels[0])
    validate_sdist(sdists[0])
    print(f"Validated source + wheel contents for agentegrity {version}")

if __name__ == "__main__":
    main()
