"""Tests for the `agentegrity report` CLI command.

Renders a serialized chain into an audit report (markdown or JSON)
with verification status, decision timeline, approvals, and a
compliance-evidence mapping. The report is supporting evidence, not a
compliance determination — the tests pin that framing too.
"""

from __future__ import annotations

import json

import pytest

from agentegrity.__main__ import main
from agentegrity.core.attestation import (
    AttestationChain,
    build_attestation_record,
)
from agentegrity.core.decision import build_decision_record
from agentegrity.core.evaluator import (
    IntegrityScore,
    LayerResult,
    PropertyScores,
)
from agentegrity.core.profile import AgentProfile


def _score() -> IntegrityScore:
    return IntegrityScore(
        composite=0.9,
        properties=PropertyScores(0.9, 0.9, 0.9, 0.9),
        layer_results=[
            LayerResult(
                layer_name="governance",
                score=0.9,
                passed=True,
                action="pass",
                details={},
            )
        ],
    )


@pytest.fixture
def chain_file(tmp_path):
    chain = AttestationChain()
    decision = build_decision_record(
        agent_id="agent-1",
        decision_point="pre_tool_use",
        candidate_action={"tool": "payment_execute"},
    )
    chain.append(decision)
    approval = build_decision_record(
        agent_id="agent-1",
        decision_point="approval",
        candidate_action={"tool": "payment_execute"},
        reasoning_chain=[
            "outcome:approved",
            "approver:tarique",
            "timed_out:false",
        ],
    )
    chain.append(approval)
    chain.append(
        build_attestation_record(AgentProfile.default(name="agent-1"), _score())
    )
    path = tmp_path / "session.chain.json"
    path.write_text(chain.to_json())
    return path


class TestReportMarkdown:
    def test_report_renders_and_exits_zero(self, chain_file, tmp_path, capsys):
        out = tmp_path / "report.md"
        code = main(["report", str(chain_file), "-o", str(out)])
        assert code == 0
        text = out.read_text()
        assert "# Agentegrity Session Audit Report" in text
        assert "## Verification" in text
        assert "## Record Timeline" in text
        assert "## Human Approvals" in text
        assert "approver:tarique" in text or "tarique" in text
        assert "EU AI Act" in text
        assert "NIST AI RMF" in text
        # Honest framing is part of the contract.
        assert "not a compliance determination" in text

    def test_report_to_stdout_without_output_flag(self, chain_file, capsys):
        code = main(["report", str(chain_file)])
        assert code == 0
        assert "# Agentegrity Session Audit Report" in capsys.readouterr().out

    def test_unpinned_signatures_disclosed(self, chain_file, capsys):
        main(["report", str(chain_file)])
        out = capsys.readouterr().out
        assert "self-vouched" in out


class TestReportJson:
    def test_json_format(self, chain_file, tmp_path):
        out = tmp_path / "report.json"
        code = main(
            ["report", str(chain_file), "--format", "json", "-o", str(out)]
        )
        assert code == 0
        data = json.loads(out.read_text())
        assert data["records"] == 3
        assert data["verification"]["chain_linkage"] is True
        assert data["approvals"][0]["decision_point"] == "approval"


class TestReportFailures:
    def test_missing_file_exits_2(self, tmp_path):
        assert main(["report", str(tmp_path / "nope.json")]) == 2

    def test_tampered_chain_reports_failure_and_exits_1(
        self, chain_file, tmp_path, capsys
    ):
        data = json.loads(chain_file.read_text())
        records = data["records"] if isinstance(data, dict) else data
        records[0]["candidate_action"] = {"tool": "something_else"}
        tampered = tmp_path / "tampered.json"
        tampered.write_text(json.dumps(data))
        code = main(["report", str(tampered)])
        assert code == 1
        out = capsys.readouterr().out
        assert "NO" in out or "FAILED" in out
