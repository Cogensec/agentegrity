#!/usr/bin/env python3
"""Recheck serialized banking evaluation evidence without changing raw runs."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from agentegrity.core.attestation import AttestationChain


def check(dataset: Path) -> dict:
    manifest = json.loads((dataset / "manifest.json").read_text())
    rows = [json.loads(line) for line in (dataset / "results.jsonl").read_text().splitlines()]
    failures: list[str] = []
    for name, expected in manifest["file_sha256"].items():
        if hashlib.sha256((dataset / name).read_bytes()).hexdigest() != expected:
            failures.append(f"digest:{name}")
    if len(rows) != manifest["actual_run_count"]:
        failures.append("row_count")
    counts: Counter[str] = Counter()
    for row in rows:
        rid = row["run_id"]
        trace = json.loads((dataset / "traces" / f"{rid}.json").read_text())
        if len(trace["steps"]) != 20 or [s["index"] for s in trace["steps"]] != list(range(1, 21)):
            failures.append(f"steps:{rid}")
        if trace["run"] != row:
            failures.append(f"row_mismatch:{rid}")
        if not row["mode"].startswith("protected"):
            continue
        records = trace["attestations"]
        key = bytes.fromhex(trace["trusted_public_key_hex"])
        chain = AttestationChain.from_dict_list(records)
        checks = {
            "hash_link": chain.verify_chain(),
            "trusted_signature": chain.verify_signatures({key})[0],
            "decision_links": chain.verify_decision_links(),
            "four_layers_precheck": bool(trace["evals"]) and
                {v["layer_name"] for v in trace["evals"][0]["layer_results"]} ==
                {"adversarial", "cortical", "governance", "recovery"},
            "first_record_tamper_rejected": False,
            "last_record_tamper_rejected": False,
            "broken_link_rejected": False,
            "missing_signature_rejected": False,
            "wrong_trust_key_rejected": not chain.verify_signatures({b"\x00" * 32})[0],
        }
        changed = copy.deepcopy(records)
        changed[0]["agent_id"] += "-tampered"
        checks["first_record_tamper_rejected"] = not AttestationChain.from_dict_list(changed).verify_signatures({key})[0]
        changed = copy.deepcopy(records)
        changed[-1]["agent_id"] += "-tampered"
        checks["last_record_tamper_rejected"] = not AttestationChain.from_dict_list(changed).verify_signatures({key})[0]
        if len(records) > 1:
            changed = copy.deepcopy(records)
            changed[1]["chain_previous"] = "0" * 64
            checks["broken_link_rejected"] = not AttestationChain.from_dict_list(changed).verify_chain()
        changed = copy.deepcopy(records)
        changed[0]["signature"] = None
        checks["missing_signature_rejected"] = not AttestationChain.from_dict_list(changed).verify_signatures({key})[0]
        for label, success in checks.items():
            counts[f"{label}_pass" if success else f"{label}_fail"] += 1
            if not success:
                failures.append(f"{label}:{rid}")
    return {
        "dataset": str(dataset), "rows": len(rows),
        "protected_rows": sum(r["mode"].startswith("protected") for r in rows),
        "checks": dict(sorted(counts.items())), "failures": failures,
        "scope": "Checks serialized records against each trace's embedded key; no external trust anchor or input-truth validation.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"rows": result["rows"], "protected_rows": result["protected_rows"], "failures": len(result["failures"])}, indent=2))
    raise SystemExit(bool(result["failures"]))
