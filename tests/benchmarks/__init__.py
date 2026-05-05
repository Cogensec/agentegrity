"""Detection benchmark harness for the AdversarialLayer.

Provides a calibration-stable way to measure detection quality
(true-positive rate, false-positive rate, precision, recall, F1) on
labelled prompt sets, broken down per attack family.

Usage from the command line::

    python scripts/run_benchmarks.py

Usage from pytest (CI smoke benchmark)::

    pytest -m benchmark

External datasets (PINT, AgentDojo, InjecAgent) plug in via
:mod:`tests.benchmarks.loaders` — set the relevant ``AGENTEGRITY_BENCH_*``
env var to a local fixture path and the loader will pick it up.
Without those vars the suite falls back to the in-repo synthetic
fixture defined in :mod:`tests.benchmarks.fixtures`.
"""

from tests.benchmarks.fixtures import (
    BenchmarkPrompt,
    benign_prompts,
    synthetic_pint_like,
)
from tests.benchmarks.harness import (
    BenchmarkResult,
    BenchmarkRow,
    format_markdown_report,
    run_suite,
)

__all__ = [
    "BenchmarkPrompt",
    "BenchmarkResult",
    "BenchmarkRow",
    "benign_prompts",
    "format_markdown_report",
    "run_suite",
    "synthetic_pint_like",
]
