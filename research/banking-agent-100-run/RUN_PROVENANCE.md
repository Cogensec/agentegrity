# Execution provenance · 20 October 2026

- Source repository: https://github.com/Cogensec/agentegrity
- Checked-out full commit: `56cfcc48d8edeb4beee1bec3948c95d20804e4f4`
- Source tree hash: `5c504340b246b46a6c89f04abd54c6f07acaa4e0`
- Repository version: `0.12.0`; editable install from the checked-out source using the local Python 3.12 environment.
- Original v0.1 research ZIP SHA-256: `17e0571c74eacb62788b7156a838366d46019417483f7cf908cd3b7e0f763c9a`. The original ZIP was extracted without editing it.
- Dependency snapshot: `dependencies.lock.txt`; portable install recipe: `requirements-recreate.txt`. The snapshot includes a machine-specific editable path, so it is not a portable lockfile.
- Telemetry: `DO_NOT_TRACK=1` set for all actual Agentegrity runs and evidence audits; no exporter configured.
- Workstation runtime: CPython 3.12.13 on macOS arm64; the exact platform string is in each run manifest.

## Execution record

1. Fixture tests: **6 passed** against the extracted research harness.
2. One-run-per-scenario protected smoke test: **10 runs completed** in `outputs/smoke-protected-20261020/`. This is diagnostic and excluded from the primary results.
3. Primary paired run: `python banking_eval.py --mode both --n 10 --seed 4815 --out outputs/paired-current-20261020`; **200 rows**, 100 baseline and 100 protected, each with a 20-stage trace. Stdout and stderr are in `run_logs/`.
4. Exploratory ablation: `python banking_eval.py --mode protected_approve_all --n 10 --seed 4815 --out outputs/approve-all-ablation-20261020`; **100 rows** and 100 traces.
5. Serialized evidence audits: `audit_evidence.py` reported **zero failures** for both datasets. It checked manifest hashes, row/trace agreement, 20-stage order, four precheck layers, hash links, signatures, decision links, and five tamper challenges per protected trace. The key used for verification is embedded in each trace.
6. Relevant upstream tests for attestation, monitor, governance, recovery, and adversarial detectors: **174 passed** after installing `pytest-asyncio`. The first attempt had 173 passed and one failed because that test dependency was missing; it passed on rerun. No source changes were made to Agentegrity.

## Known attribution limits

All primary precheck composite actions were `escalate`, so a scenario-aware callback determined all allow/deny outcomes. The ablation accepts every escalation and is exploratory. The fixture's synthetic approval field and callback are not real human behavior. Repetition varies synthetic IDs and amounts but reuses each attack template. Evidence signatures prove record integrity against the included per-run keys; they do not prove ground-truth correctness or independent publication provenance.
