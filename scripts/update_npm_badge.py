#!/usr/bin/env python3
"""Sum all-time npm downloads across every @agentegrity package and write a
shields.io endpoint badge JSON to badges/npm-downloads.json.

Run by .github/workflows/npm-downloads-badge.yml on a daily cron.
"""

import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

SCOPE = "agentegrity"
# npm's downloads API caps range queries at ~18 months, so we chunk.
CHUNK_DAYS = 540
# Earliest date we care about (npm has no data before a package existed anyway).
START = date(2025, 1, 1)
BADGE_PATH = "badges/npm-downloads.json"

# Known packages as of 2026-07 — used as a floor so a flaky search API can
# never shrink the tally. New scope packages are picked up automatically.
KNOWN_PACKAGES = [
    "@agentegrity/claude-sdk",
    "@agentegrity/client",
    "@agentegrity/crewai",
    "@agentegrity/google-adk",
    "@agentegrity/langchain",
    "@agentegrity/openai-agents",
    "@agentegrity/vercel-ai",
]


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "agentegrity-badge-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def discover_packages() -> list[str]:
    """Find every package in the @agentegrity scope via the registry search API."""
    discovered: set[str] = set()
    try:
        url = (
            "https://registry.npmjs.org/-/v1/search?"
            + urllib.parse.urlencode({"text": f"@{SCOPE}", "size": 250})
        )
        data = get_json(url)
        discovered = {
            o["package"]["name"]
            for o in data.get("objects", [])
            if o["package"]["name"].startswith(f"@{SCOPE}/")
        }
    except Exception as e:  # noqa: BLE001 — badge job should degrade, not die
        print(f"Search API failed ({e}); falling back to pinned list.", file=sys.stderr)
    return sorted(discovered | set(KNOWN_PACKAGES))


def total_downloads(pkg: str) -> int:
    total = 0
    cursor = START
    today = date.today()
    while cursor <= today:
        end = min(cursor + timedelta(days=CHUNK_DAYS - 1), today)
        url = f"https://api.npmjs.org/downloads/point/{cursor}:{end}/{pkg}"
        try:
            total += get_json(url).get("downloads", 0) or 0
        except urllib.error.HTTPError as e:
            # 404 = no data in this window (package didn't exist yet). Skip.
            if e.code != 404:
                raise
        cursor = end + timedelta(days=1)
    return total


def humanize(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,}"


def main() -> None:
    packages = discover_packages()
    per_pkg = {pkg: total_downloads(pkg) for pkg in packages}
    grand_total = sum(per_pkg.values())

    for pkg, n in per_pkg.items():
        print(f"{pkg}: {n:,}")
    print(f"TOTAL: {grand_total:,}")

    badge = {
        "schemaVersion": 1,
        "label": "npm downloads",
        "message": humanize(grand_total),
        "color": "cb3837",  # npm red; swap for Cogensec Copper if preferred
        "namedLogo": "npm",
    }
    with open(BADGE_PATH, "w") as f:
        json.dump(badge, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
