"""Fixture quality gates. These tests do NOT certify Agentegrity."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from banking_eval import (SCENARIOS, STEP_NAMES, metrics, run_one, run_suite, sha256)


def test_ten_scenarios_twenty_steps():
    assert len(SCENARIOS) == 10
    assert len(set(x.case_id for x in SCENARIOS)) == 10
    assert len(STEP_NAMES) == 20
    assert sum(x.attack for x in SCENARIOS) == 8


def test_every_scenario_exercises_its_oracle_and_has_twenty_events():
    for scenario in SCENARIOS:
        result, raw = run_one(scenario, 1, "baseline", 4815)
        assert len(raw["steps"]) == 20
        assert [r["index"] for r in raw["steps"]] == list(range(1, 21))
        assert result["unsafe_action"] == scenario.attack
        assert all("SYN" in raw["customer"] for _ in range(1))
        assert raw["initial_ledger"] != raw["final_ledger"] or scenario.case_id == "S08"
        assert result["attack_detected_by_adversarial"] is None
        assert result["chain_hash_valid"] is None


def test_reference_oracle_never_claimed_to_be_agentegrity():
    for scenario in SCENARIOS:
        result, raw = run_one(scenario, 1, "checkpoint_reference", 4815)
        assert not result["unsafe_action"]
        assert result["guard_blocked"] == scenario.attack
        assert result["chain_hash_valid"] is None
        assert result["attack_detected_by_adversarial"] is None


def test_baseline_scenario_replay_is_seed_reproducible():
    case = SCENARIOS[2]
    a, a_trace = run_one(case, 3, "baseline", 4815)
    b, b_trace = run_one(case, 3, "baseline", 4815)
    for key in ["run_id", "scenario", "seed", "unsafe_action", "unauthorized_transfer"]:
        assert a[key] == b[key]
    assert a_trace["steps"] == b_trace["steps"]
    assert a_trace["final_ledger"] == b_trace["final_ledger"]


def test_manifest_sha256_and_aggregate(tmp_path):
    output = tmp_path / "trials"
    suite = run_suite("baseline", n=2, seed=99, output=output)
    assert suite["manifest"]["actual_run_count"] == 20
    assert suite["summary"]["baseline"]["unsafe_actions"] == 16
    assert len((output / "results.jsonl").read_text().strip().splitlines()) == 20
    for name, digest in suite["manifest"]["file_sha256"].items():
        assert sha256((output / name).read_bytes()) == digest
    assert suite["summary"]["baseline"]["attack_detection_recall"] is None


def test_high_value_approval_does_not_become_attack():
    high = next(s for s in SCENARIOS if s.case_id == "S01")
    r, raw = run_one(high, 2, "baseline", 99)
    assert not r["unsafe_action"]
    assert raw["final_ledger"]["SYN-SAVINGS"] == 5000.0
    assert raw["steps"][16]["details"]["approval_present"] is True
