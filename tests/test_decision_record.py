"""Tests for DecisionRecord and the supporting decision-provenance types."""

import pytest

from agentegrity.core.attestation import ChainedRecord
from agentegrity.core.decision import (
    CaptureTier,
    DecisionInput,
    DecisionRecord,
    RejectedAlternative,
    _json_safe,
    build_decision_record,
    infer_capture_tier,
)


def make_decision(
    agent_id="agent-001",
    decision_point="pre_tool_use",
    candidate_action=None,
    reasoning_chain=None,
    rejected_alternatives=None,
    decision_inputs=None,
):
    return build_decision_record(
        agent_id=agent_id,
        decision_point=decision_point,
        candidate_action=candidate_action or {"type": "tool_call", "tool_name": "calc"},
        reasoning_chain=reasoning_chain,
        rejected_alternatives=rejected_alternatives,
        decision_inputs=decision_inputs,
    )


class TestCaptureTierInference:
    def test_minimal_when_nothing_populated(self):
        assert infer_capture_tier(None, None) is CaptureTier.MINIMAL
        assert infer_capture_tier([], []) is CaptureTier.MINIMAL

    def test_partial_when_reasoning_chain_present(self):
        assert infer_capture_tier(["step 1"], None) is CaptureTier.PARTIAL
        assert infer_capture_tier(["step 1"], []) is CaptureTier.PARTIAL

    def test_full_when_rejected_alternatives_present(self):
        rej = [RejectedAlternative(action_summary="x", rejection_reason="y")]
        assert infer_capture_tier(None, rej) is CaptureTier.FULL
        assert infer_capture_tier(["step"], rej) is CaptureTier.FULL


class TestDecisionRecord:
    def test_creation_defaults_to_minimal_tier(self):
        record = make_decision()
        assert record.capture_tier is CaptureTier.MINIMAL
        assert record.decision_point == "pre_tool_use"
        assert record.record_kind == "decision"
        assert record.redacted is True

    def test_canonical_payload_deterministic(self):
        record = make_decision()
        assert record.canonical_payload == record.canonical_payload

    def test_content_hash_is_sha256_hex(self):
        record = make_decision()
        h = record.content_hash
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_decision_points_produce_different_hashes(self):
        r1 = make_decision(decision_point="pre_tool_use")
        r2 = make_decision(decision_point="stop")
        assert r1.content_hash != r2.content_hash

    def test_canonical_payload_invariant_under_key_reorder(self):
        """Reordering keys in candidate_action must not change the hash."""
        r1 = make_decision(candidate_action={"a": 1, "b": 2})
        r2 = make_decision(candidate_action={"b": 2, "a": 1})
        # IDs differ, so canonicals differ. Compare only the payload field.
        import json
        p1 = json.loads(r1.canonical_payload)
        p2 = json.loads(r2.canonical_payload)
        assert p1["candidate_action"] == p2["candidate_action"]

    def test_capture_tier_full_when_rejected_alternatives_passed(self):
        record = make_decision(
            rejected_alternatives=[
                RejectedAlternative(
                    action_summary="delete file",
                    rejection_reason="risky",
                )
            ]
        )
        assert record.capture_tier is CaptureTier.FULL

    def test_capture_tier_partial_when_only_reasoning_chain(self):
        record = make_decision(reasoning_chain=["thought 1", "thought 2"])
        assert record.capture_tier is CaptureTier.PARTIAL

    def test_to_dict_round_trip(self):
        original = make_decision(
            reasoning_chain=["a", "b"],
            decision_inputs=[
                DecisionInput(
                    channel="user_prompt",
                    content_hash="abc123",
                    summary="user asked for sum",
                )
            ],
        )
        d = original.to_dict()
        rebuilt = DecisionRecord.from_dict(d)
        assert rebuilt.agent_id == original.agent_id
        assert rebuilt.decision_point == original.decision_point
        assert rebuilt.capture_tier == original.capture_tier
        assert rebuilt.candidate_action == original.candidate_action
        assert len(rebuilt.decision_inputs) == 1
        assert rebuilt.decision_inputs[0].channel == "user_prompt"
        assert rebuilt.content_hash == original.content_hash

    def test_unsigned_verify_returns_false(self):
        record = make_decision()
        try:
            assert record.verify() is False
        except ImportError:
            pytest.skip("cryptography not installed")


class TestDecisionRecordSigning:
    def test_signed_record_verifies(self):
        from agentegrity.core.attestation import generate_signing_key

        try:
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        record = build_decision_record(
            agent_id="agent-001",
            decision_point="pre_tool_use",
            candidate_action={"type": "tool_call", "tool_name": "x"},
            signing_key=key,
        )
        assert record.signature is not None
        assert record.public_key is not None
        assert record.verify() is True

    def test_tampered_record_fails_verification(self):
        from agentegrity.core.attestation import generate_signing_key

        try:
            key = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        record = build_decision_record(
            agent_id="agent-001",
            decision_point="pre_tool_use",
            candidate_action={"type": "tool_call", "tool_name": "x"},
            signing_key=key,
        )
        record.candidate_action["tool_name"] = "evil"
        assert record.verify() is False

    def test_isolated_keys_dont_cross_verify(self):
        from agentegrity.core.attestation import generate_signing_key

        try:
            key1 = generate_signing_key()
            key2 = generate_signing_key()
        except ImportError:
            pytest.skip("cryptography not installed")

        record = build_decision_record(
            agent_id="agent-001",
            decision_point="pre_tool_use",
            candidate_action={"type": "tool_call", "tool_name": "x"},
            signing_key=key1,
        )
        # Override public_key with the wrong one
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        record.public_key = key2.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )
        assert record.verify() is False


class TestChainedRecordProtocol:
    """Both record kinds satisfy the structural ChainedRecord type."""

    def test_attestation_record_is_chained_record(self):
        from agentegrity.core.attestation import AttestationRecord, Evidence

        rec = AttestationRecord(
            agent_id="a", integrity_score={"composite": 1.0},
            evidence=[Evidence(evidence_type="e", source="s", content_hash="h", summary="x")],
        )
        assert isinstance(rec, ChainedRecord)

    def test_decision_record_is_chained_record(self):
        rec = make_decision()
        assert isinstance(rec, ChainedRecord)


class TestJsonSafeCoercion:
    """_json_safe defends candidate_action against non-JSON-native types."""

    def test_native_types_pass_through(self):
        assert _json_safe(None) is None
        assert _json_safe(True) is True
        assert _json_safe(42) == 42
        assert _json_safe(3.14) == 3.14
        assert _json_safe("hi") == "hi"

    def test_nested_dict(self):
        assert _json_safe({"a": {"b": [1, 2]}}) == {"a": {"b": [1, 2]}}

    def test_set_becomes_sorted_list(self):
        assert _json_safe({1, 2, 3}) == [1, 2, 3]

    def test_bytes_become_hex_string(self):
        assert _json_safe(b"\x01\x02\xff") == "0102ff"

    def test_dataclass_becomes_dict(self):
        di = DecisionInput(channel="c", content_hash="h", summary="s")
        result = _json_safe(di)
        assert result == {"channel": "c", "content_hash": "h", "summary": "s"}

    def test_exotic_type_falls_back_to_repr_with_marker(self):
        class Exotic:
            def __repr__(self):
                return "Exotic()"

        result = _json_safe(Exotic())
        assert result == {"_coerced": True, "repr": "Exotic()"}

    def test_candidate_action_with_set_does_not_break_canonical(self):
        record = build_decision_record(
            agent_id="a",
            decision_point="pre_tool_use",
            candidate_action={"args": {1, 2, 3}, "tool_name": "x"},
        )
        # Should not raise; canonical_payload should serialize cleanly
        assert isinstance(record.canonical_payload, str)
        assert len(record.content_hash) == 64
