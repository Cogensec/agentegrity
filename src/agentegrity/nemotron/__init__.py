"""First-class Nemotron support for Agentegrity.

A standalone Nemotron model client (mock + live) plus a YAML-driven benchmark
profile that scores the model through the existing Agentegrity layers and
attestation system. See :mod:`agentegrity.nemotron.adapter` for the client and
:mod:`agentegrity.nemotron.benchmark` for the runner.
"""

from __future__ import annotations

from agentegrity.nemotron.adapter import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    NemotronAdapter,
    NemotronResponse,
    ToolCall,
)
from agentegrity.nemotron.benchmark import (
    BenchmarkReport,
    CaseResult,
    CategoryResult,
    NemotronBenchmarkRunner,
    ProfileMeta,
    TestCase,
    grade,
    load_profile,
    readiness,
)
from agentegrity.nemotron.report import to_json, to_markdown

__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "BenchmarkReport",
    "CaseResult",
    "CategoryResult",
    "NemotronAdapter",
    "NemotronBenchmarkRunner",
    "NemotronResponse",
    "ProfileMeta",
    "TestCase",
    "ToolCall",
    "grade",
    "load_profile",
    "readiness",
    "run_benchmark",
    "to_json",
    "to_markdown",
]


def run_benchmark(*,
    profile_path: str,
    mock: bool = False,
    endpoint: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
) -> BenchmarkReport:
    """Load a profile, run the full benchmark, and return the report.

    Thin orchestration helper shared by the CLI and tests. CLI-supplied
    overrides take precedence over the manifest defaults.
    """
    meta, cases = load_profile(profile_path)
    if endpoint:
        meta.endpoint = endpoint
    if model:
        meta.model = model
    if api_key_env:
        meta.api_key_env = api_key_env

    adapter = NemotronAdapter(
        model=meta.model or DEFAULT_MODEL,
        endpoint=meta.endpoint or DEFAULT_ENDPOINT,
        api_key_env=meta.api_key_env,
        mock=mock,
    )
    runner = NemotronBenchmarkRunner(meta)
    return runner.run(adapter, cases)
