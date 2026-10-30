"""U4 synthetic reference contracts: actual executor veto in two domains."""

from examples.enterprise_commerce_boundaries import run_demo


def test_enterprise_and_commerce_domains_block_all_untrusted_action_paths() -> None:
    outcomes = run_demo()
    assert set(outcomes) == {"enterprise", "commerce"}
    expected_actions = {
        "enterprise": {"resource_id": "enterprise-resource-1", "case_status": "resolved"},
        "commerce": {"resource_id": "commerce-resource-1", "amount_cents": 2500},
    }
    for domain, outcome in outcomes.items():
        assert outcome["domain"] == domain
        assert outcome["executed"] == [expected_actions[domain]]
        assert outcome["denied"]["retrieval"] == "untrusted_control_source"
        assert outcome["denied"]["peer"] == "untrusted_control_source"
        assert outcome["denied"]["contamination"] == "data_promoted_to_control"
        assert outcome["denied"]["resource_substitution"] == "resource_not_authorized"
        assert outcome["denied"]["action_retrieval"] == "untrusted_control_source"
        assert outcome["denied"]["action_peer"] == "untrusted_control_source"
        assert outcome["denied"]["action_contamination"] == "data_promoted_to_control"
        assert outcome["denied"]["action_missing_lineage"] == "missing_control_provenance"
        assert outcome["denied"]["action_omitted"] == "protected_field_missing"
        assert outcome["receipt_status"] == "pending"
        assert outcome["independently_verified"] is False


def test_enterprise_and_commerce_actions_are_process_local_and_host_scoped() -> None:
    # Each demo builds a fresh mandate, registry, provenance source map, and
    # in-memory executor journal. No customer or cross-run state is shared.
    first = run_demo()
    second = run_demo()
    assert first == second
    for result in first.values():
        assert len(result["executed"]) == 1
        assert len(result["denied"]) == 9

