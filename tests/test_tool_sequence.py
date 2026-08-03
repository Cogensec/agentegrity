"""Behavioral sequence detection: sensitive-read followed by
external-send within one session.

Multi-step exfiltration is benign at every step — "read the
transaction history", then three steps later "post to a webhook". No
content pattern can see it; the *order of tool calls* can. The
detector matches tool names against operator-configurable categories
using the same exact/glob/MCP-suffix semantics as GOV-001.
"""

from __future__ import annotations

import asyncio

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.evaluator import IntegrityEvaluator
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.layers.adversarial import (
    AdversarialLayer,
    ToolCategories,
)


def _profile() -> AgentProfile:
    return AgentProfile(
        name="t",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


def _sequence_threats(result) -> list[dict]:
    return [
        t for t in result.details["threats"]
        if t["threat_type"] == "exfiltration_sequence"
    ]


class TestToolSequenceDetection:
    def test_read_then_send_flags(self):
        layer = AdversarialLayer()
        result = layer.evaluate(
            _profile(),
            {
                "tool_call_history": [
                    "get_weather",
                    "transaction_history",
                    "summarize",
                    "send_email",
                ]
            },
        )
        threats = _sequence_threats(result)
        assert len(threats) == 1
        assert threats[0]["channel"] == "tool_sequence"
        assert "transaction_history->send_email" in threats[0]["indicators"]

    def test_send_before_read_does_not_flag(self):
        layer = AdversarialLayer()
        result = layer.evaluate(
            _profile(),
            {"tool_call_history": ["send_email", "transaction_history"]},
        )
        assert _sequence_threats(result) == []

    def test_read_only_or_send_only_does_not_flag(self):
        layer = AdversarialLayer()
        for history in (
            ["transaction_history", "summarize"],
            ["summarize", "send_email"],
        ):
            result = layer.evaluate(
                _profile(), {"tool_call_history": history}
            )
            assert _sequence_threats(result) == [], history

    def test_mcp_namespaced_names_match(self):
        layer = AdversarialLayer()
        result = layer.evaluate(
            _profile(),
            {
                "tool_call_history": [
                    "mcp__gmail__read_messages",
                    "mcp__slack__send_message",
                ]
            },
        )
        assert len(_sequence_threats(result)) == 1

    def test_custom_categories(self):
        layer = AdversarialLayer(
            tool_categories=ToolCategories(
                reads_sensitive=frozenset({"load_soc2_evidence"}),
                sends_external=frozenset({"jira_create_ticket"}),
            )
        )
        result = layer.evaluate(
            _profile(),
            {
                "tool_call_history": [
                    "load_soc2_evidence",
                    "jira_create_ticket",
                ]
            },
        )
        assert len(_sequence_threats(result)) == 1
        # Defaults are replaced, not extended.
        result2 = layer.evaluate(
            _profile(),
            {"tool_call_history": ["transaction_history", "send_email"]},
        )
        assert _sequence_threats(result2) == []

    def test_detection_can_be_disabled(self):
        layer = AdversarialLayer(detect_tool_sequences=False)
        result = layer.evaluate(
            _profile(),
            {"tool_call_history": ["transaction_history", "send_email"]},
        )
        assert _sequence_threats(result) == []

    def test_multiple_pairs_aggregate_to_one_assessment(self):
        layer = AdversarialLayer()
        result = layer.evaluate(
            _profile(),
            {
                "tool_call_history": [
                    "transaction_history",
                    "read_email",
                    "send_email",
                    "http_post",
                ]
            },
        )
        threats = _sequence_threats(result)
        assert len(threats) == 1
        assert len(threats[0]["indicators"]) >= 2


class TestAdapterExposesHistory:
    def test_tool_call_history_in_evaluation_context(self):
        adapter = _BaseAdapter(
            profile=_profile(), evaluator=IntegrityEvaluator(layers=[])
        )
        loop = asyncio.new_event_loop()
        for tool in ("read_email", "send_email"):
            loop.run_until_complete(
                adapter.on_event(
                    "pre_tool_use", {"tool_name": tool, "tool_input": {}}
                )
            )
        context = adapter._buffer.to_evaluation_context()
        assert context["tool_call_history"] == ["read_email", "send_email"]
