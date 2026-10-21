#!/usr/bin/env python3
"""Credential-free runtime canary for CrewAI's patched MCP/uv upgrade path.

This does NOT remove CrewAI's published dependency constraints, prove all
CrewAI features work, or address ChromaDB's unpatched vulnerabilities.
It gives upstream maintainers reproducible smoke-test evidence for changing
the published mcp and crewai-cli/uv requirements.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def installed(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError as exc:
        raise RuntimeError(f"expected package not installed: {name}") from exc


def main() -> int:
    versions = {
        "crewai": installed("crewai"),
        "crewai-cli": installed("crewai-cli"),
        "chromadb": installed("chromadb"),
        "mcp": installed("mcp"),
        "uv": installed("uv"),
        "agentegrity": installed("agentegrity"),
    }
    print("Installed upstream versions:", json.dumps(versions, sort_keys=True))

    # Test public APIs used by the CrewAI/Agentegrity integration.
    from crewai.events import (  # noqa: PLC0415
        CrewKickoffStartedEvent,
        TaskStartedEvent,
        ToolUsageFinishedEvent,
        crewai_event_bus,
    )
    from crewai.hooks import register_before_tool_call_hook  # noqa: PLC0415
    from crewai.mcp.config import MCPServerHTTP  # noqa: PLC0415
    from crewai.mcp.transports.http import HTTPTransport  # noqa: PLC0415
    from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415
    from agentegrity.crewai import instrument, reset  # noqa: PLC0415

    for obj in (
        CrewKickoffStartedEvent,
        TaskStartedEvent,
        ToolUsageFinishedEvent,
        crewai_event_bus,
        register_before_tool_call_hook,
        streamablehttp_client,
    ):
        if obj is None:
            raise RuntimeError("CrewAI/MCP public runtime contract is unavailable")
    config = MCPServerHTTP(url="https://example.invalid/mcp")
    if "example.invalid" not in str(config.url):
        raise RuntimeError("CrewAI HTTP MCP config lost its endpoint")
    transport = HTTPTransport(url="https://example.invalid/mcp")
    if transport is None:
        raise RuntimeError("CrewAI HTTP transport could not initialize")
    adapter = instrument()
    adapter.close()
    reset()

    # uv is a separate executable and metadata dependency, not a Python API.
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        raise RuntimeError("CrewAI CLI uv binary is missing")
    proc = subprocess.run([uv_bin, "--version"], check=True, text=True, capture_output=True)
    if not proc.stdout.startswith("uv "):
        raise RuntimeError(f"unexpected uv CLI output: {proc.stdout!r}")
    print(f"uv executable: {proc.stdout.strip()}")
    print("PASS: CrewAI adapter, MCP client/transport, and uv CLI import smoke")
    print("WARNING: does not prove full MCP functionality or patch CrewAI metadata.")
    print("WARNING: ChromaDB 1.5.9 known advisories remain open.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAILED: CrewAI patched dependency canary: {exc}", file=sys.stderr)
        raise
