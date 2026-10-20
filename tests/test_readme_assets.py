"""Documented README asset and example checks (offline, no network)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_illustrations_are_valid_accessible_svg():
    for name in ("action-authority-terminal-demo.svg", "runtime-integrity-architecture.svg"):
        path = ROOT / "assets" / name
        tree = ET.parse(path)
        root = tree.getroot()
        assert root.tag == "{http://www.w3.org/2000/svg}svg"
        assert root.get("viewBox"), f"{name}: missing scalable viewBox"
        assert root.find("{http://www.w3.org/2000/svg}title") is not None
        assert root.find("{http://www.w3.org/2000/svg}desc") is not None


def test_readme_internal_links_point_to_existing_files():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = set(re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", readme))
    for target in targets:
        if target.startswith(("https://", "http://", "mailto:")):
            continue
        assert (ROOT / target).exists(), f"Broken README link: {target}"
    for img in (
        "assets/action-authority-terminal-demo.svg",
        "assets/runtime-integrity-architecture.svg",
    ):
        assert f'src="{img}"' in readme


def test_readme_demo_is_honest_about_synthetic_replay():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    demo = (ROOT / "assets" / "action-authority-terminal-demo.svg").read_text(
        encoding="utf-8"
    )
    assert "not a literal screen recording" in readme
    assert "in-memory synthetic ledger" in readme
    assert "Out-of-scope action: denied" in demo
    assert "Before simulated evidence verification: pending" in demo
    assert "After simulated evidence verification: confirmed" in demo
    assert "No real bank" in demo


def test_adoption_badges_and_framework_logos_remain_visible():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "static.pepy.tech/personalized-badge/agentegrity" in readme
    assert "img.shields.io/npm/dm/@agentegrity/client" in readme
    assert "badges%2Fnpm-downloads.json" in readme
    assert (ROOT / ".github" / "workflows" / "npm-downloads-badge.yml").exists()
    logos = (
        "anthropics", "langchain-ai", "openai", "crewAIInc",
        "google", "microsoft", "agno-agi", "aws", "vercel",
    )
    for org in logos:
        assert f"https://github.com/{org}.png" in readme
    assert "logos identify third-party products" in readme
