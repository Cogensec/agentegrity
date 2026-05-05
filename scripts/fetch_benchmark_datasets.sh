#!/usr/bin/env bash
#
# Fetch the public, freely-licensed prompt-attack datasets the
# detection benchmark loaders know how to read, into
# ``tests/benchmarks/data/<dataset>/``. After running this script
# the relevant ``AGENTEGRITY_BENCH_*`` env var is set and the
# pytest -m benchmark tests pick the data up automatically.
#
# Datasets fetched:
#   * InjecAgent (UIUC, Apache-2.0):
#       https://github.com/uiuc-kang-lab/InjecAgent
#       Two JSON files: test_cases_dh_base.json (510 records),
#       test_cases_ds_base.json (544 records) → ~2,108 prompts.
#
# Datasets NOT fetched here (require manual access):
#   * Lakera PINT — full dataset is gated; only an example file is
#     in the public repo. Set AGENTEGRITY_BENCH_PINT yourself if
#     you have access.
#   * AgentDojo — install the python package and use its tasks API
#     directly; this script doesn't shim that.
#
# The script is idempotent: rerunning skips files that are already
# present unless --force is passed.
#
# Usage:
#   ./scripts/fetch_benchmark_datasets.sh
#   ./scripts/fetch_benchmark_datasets.sh --force
#
# Then:
#   export AGENTEGRITY_BENCH_INJECAGENT="$(pwd)/tests/benchmarks/data/injecagent"
#   pytest -m benchmark -v
#   python scripts/run_benchmarks.py --all > bench-report.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_ROOT="${REPO_ROOT}/tests/benchmarks/data"
FORCE=0

if [[ "${1:-}" == "--force" ]]; then
    FORCE=1
fi

mkdir -p "${DATA_ROOT}/injecagent"

INJECAGENT_BASE="https://raw.githubusercontent.com/uiuc-kang-lab/InjecAgent/main/data"
INJECAGENT_FILES=(
    "test_cases_dh_base.json"
    "test_cases_ds_base.json"
)

echo "InjecAgent → ${DATA_ROOT}/injecagent/"
for fname in "${INJECAGENT_FILES[@]}"; do
    target="${DATA_ROOT}/injecagent/${fname}"
    if [[ -f "${target}" && "${FORCE}" -eq 0 ]]; then
        size=$(wc -c < "${target}")
        echo "  ✓ ${fname} already present (${size} bytes) — pass --force to re-fetch"
        continue
    fi
    url="${INJECAGENT_BASE}/${fname}"
    echo "  → ${url}"
    if ! curl --fail --silent --show-error --location --max-time 30 \
            --output "${target}" "${url}"; then
        echo "  ✗ Failed to fetch ${fname}" >&2
        exit 1
    fi
    size=$(wc -c < "${target}")
    echo "  ✓ ${fname} (${size} bytes)"
done

cat <<EOF

Done. To run the detection benchmark against the fetched data:

    export AGENTEGRITY_BENCH_INJECAGENT="${DATA_ROOT}/injecagent"
    python -m pytest tests/test_benchmarks.py::TestInjecAgentBenchmark -v
    python scripts/run_benchmarks.py --all > bench-report.md

The data files are gitignored (see tests/benchmarks/data/.gitignore)
so they won't pollute the repo. PINT and AgentDojo require manual
configuration; see the script header for details.
EOF
