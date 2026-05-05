#!/usr/bin/env python3
"""Run the AdversarialLayer detection benchmark and print a markdown report.

Usage::

    python scripts/run_benchmarks.py            # synthetic suite only
    python scripts/run_benchmarks.py --all      # synthetic + every external
                                                #  loader that has fixtures

External datasets are loaded via the env vars documented in
``tests/benchmarks/loaders/__init__.py``. When a loader returns no
prompts the suite is silently skipped — the report still includes the
synthetic suite so cron output is always non-empty.

Pipe to ``> bench-report.md`` (or upload as a CI artifact) for a
human-readable per-suite + per-family breakdown.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the in-repo `tests` package importable when running from the repo
# root or from an installed checkout.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.benchmarks import (  # noqa: E402
    BenchmarkResult,
    format_markdown_report,
    run_suite,
    synthetic_pint_like,
)
from tests.benchmarks.loaders import (  # noqa: E402
    load_agentdojo,
    load_injecagent,
    load_pint,
)


def _maybe_run(name: str, loader) -> BenchmarkResult | None:
    prompts = loader()
    if not prompts:
        print(
            f"# {name}: skipped (loader returned no prompts — "
            f"AGENTEGRITY_BENCH_{name.upper()} unset?)",
            file=sys.stderr,
        )
        return None
    return run_suite(prompts, suite_name=name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also run every external-dataset loader that has fixtures.",
    )
    args = parser.parse_args()

    results: list[BenchmarkResult] = []
    results.append(
        run_suite(synthetic_pint_like(), suite_name="synthetic_pint_like")
    )
    if args.all:
        for name, loader in (
            ("pint", load_pint),
            ("agentdojo", load_agentdojo),
            ("injecagent", load_injecagent),
        ):
            r = _maybe_run(name, loader)
            if r is not None:
                results.append(r)

    print(format_markdown_report(results))
    # Non-zero exit if the synthetic suite regresses below the
    # calibrated floor — gives ops a CI signal even outside pytest.
    synth = results[0]
    if synth.aggregate.tpr < 0.95 or synth.aggregate.fpr > 0.05:
        print(
            f"\n!!! Calibrated thresholds breached: "
            f"TPR={synth.aggregate.tpr:.3f} FPR={synth.aggregate.fpr:.3f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
