"""Tests for Phase 2: topology surfaced as Evidence + new canonical events.

v0.8's key architectural choice: do NOT bake topology_id into
AttestationRecord canonical payload. Topology lives as
``Evidence(evidence_type="topology", ...)`` so the canonical schema
is unchanged (no canonical-payload break).
"""

import asyncio

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.attestation import (
    AttestationChain,
    AttestationRecord,
    Evidence,
    build_attestation_record,
)
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.core.topology import (
    AgentMember,
    AgentRole,
    AgentTopology,
    TopologyChange,
    TopologyKind,
)


def _profile():
    return AgentProfile(
        name="phase2-evidence",
        agent_type=AgentType.MULTI_AGENT,
        capabilities=["tool_use", "multi_agent_comm"],
        deployment_context=DeploymentContext.MULTI_AGENT,
        risk_tier=RiskTier.MEDIUM,
    )


def _score():
    from agentegrity.core.evaluator import (
        IntegrityScore,
        LayerResult,
        PropertyScores,
    )
    return IntegrityScore(
        composite=0.85,
        properties=PropertyScores(adversarial_coherence=0.9),
        layer_results=[
            LayerResult(
                layer_name="adversarial",
                score=0.9,
                passed=True,
                action="pass",
                details={},
                latency_ms=1.0,
            )
        ],
    )


def _topology():
    return AgentTopology(
        kind=TopologyKind.HUB_SPOKE,
        members=(
            AgentMember(
                agent_id="lead-1",
                name="Leader",
                role=AgentRole.LEADER,
                capabilities=("tool_use",),
            ),
            AgentMember(
                agent_id="m-1",
                name="Worker A",
                role=AgentRole.MEMBER,
                parent_id="lead-1",
                capabilities=("tool_use",),
            ),
        ),
        comm_channels=frozenset({"peer_messages"}),
    )


def _make_adapter():
    return _BaseAdapter(profile=_profile())


class TestTopologyEvidenceOnAttestation:
    def test_topology_evidence_added_when_topology_passed(self):
        topology = _topology()
        record = build_attestation_record(
            _profile(), _score(), topology=topology
        )
        topo_evidence = [
            e for e in record.evidence if e.evidence_type == "topology"
        ]
        assert len(topo_evidence) == 1
        assert topo_evidence[0].source == topology.topology_id
        assert topo_evidence[0].content_hash == topology.content_hash()
        assert "hub_spoke" in topo_evidence[0].summary
        assert "2 members" in topo_evidence[0].summary

    def test_no_topology_evidence_when_topology_absent(self):
        record = build_attestation_record(_profile(), _score())
        topo_evidence = [
            e for e in record.evidence if e.evidence_type == "topology"
        ]
        assert topo_evidence == []

    def test_topology_change_evidence_added(self):
        topology1 = _topology()
        new_member = AgentMember(
            agent_id="m-2",
            name="Worker B",
            role=AgentRole.MEMBER,
            parent_id="lead-1",
            capabilities=("tool_use",),
        )
        topology2 = topology1.with_member(new_member)
        change = TopologyChange.between(topology1, topology2)

        record = build_attestation_record(
            _profile(),
            _score(),
            topology=topology2,
            topology_change=change,
        )
        change_evidence = [
            e for e in record.evidence if e.evidence_type == "topology_change"
        ]
        assert len(change_evidence) == 1
        assert change_evidence[0].source == topology1.topology_id
        assert change_evidence[0].content_hash == topology2.content_hash()
        assert "+1" in change_evidence[0].summary
        assert "-0" in change_evidence[0].summary

    def test_canonical_payload_schema_unchanged(self):
        """Topology Evidence must NOT change the AttestationRecord
        canonical-payload schema. Existing v0.7 tests must still
        find the same keys."""
        topology = _topology()
        record = build_attestation_record(
            _profile(), _score(), topology=topology
        )
        # The canonical_payload still has its existing keys.
        import json
        payload = json.loads(record.canonical_payload)
        expected_keys = {
            "record_kind", "record_id", "agent_id", "timestamp",
            "integrity_score", "layer_states", "evidence",
            "chain_previous",
        }
        assert set(payload.keys()) == expected_keys
        # Evidence is a list; topology appears as one entry.
        evidence_types = {e["evidence_type"] for e in payload["evidence"]}
        assert "topology" in evidence_types


class TestSetTopologyOnAdapter:
    def test_set_topology_emits_topology_declared(self):
        adapter = _make_adapter()
        topology = _topology()
        adapter.set_topology(topology, my_role=AgentRole.LEADER)

        events = [e for e in adapter.events if e.event_type == "topology_declared"]
        assert len(events) == 1
        assert events[0].data["topology"]["topology_id"] == topology.topology_id

    def test_set_topology_triggers_attestation_with_topology_evidence(self):
        adapter = _make_adapter()
        topology = _topology()
        adapter.set_topology(topology, my_role=AgentRole.LEADER)

        attestations = [
            r for r in adapter.attestation_chain.records
            if isinstance(r, AttestationRecord)
        ]
        assert len(attestations) == 1
        topo_evidence = [
            e for e in attestations[0].evidence if e.evidence_type == "topology"
        ]
        assert len(topo_evidence) == 1
        assert topo_evidence[0].source == topology.topology_id

    def test_set_topology_twice_with_change_emits_topology_change(self):
        adapter = _make_adapter()
        topology1 = _topology()
        adapter.set_topology(topology1, my_role=AgentRole.LEADER)

        new_member = AgentMember(
            agent_id="m-2",
            name="Worker B",
            role=AgentRole.MEMBER,
            parent_id="lead-1",
            capabilities=("tool_use",),
        )
        topology2 = topology1.with_member(new_member)
        adapter.set_topology(topology2, my_role=AgentRole.LEADER)

        change_events = [
            e for e in adapter.events if e.event_type == "topology_change"
        ]
        assert len(change_events) == 1
        # Second attestation should carry both topology and
        # topology_change Evidence.
        attestations = [
            r for r in adapter.attestation_chain.records
            if isinstance(r, AttestationRecord)
        ]
        assert len(attestations) == 2
        ev_types_2 = {e.evidence_type for e in attestations[1].evidence}
        assert "topology" in ev_types_2
        assert "topology_change" in ev_types_2

    def test_set_topology_twice_same_structure_is_noop(self):
        """Calling set_topology with structurally identical topology
        (same content_hash) doesn't emit topology_change."""
        adapter = _make_adapter()
        topology = _topology()
        adapter.set_topology(topology)
        # Build a SECOND topology with the same structure but a
        # different topology_id (would fire as a change since
        # previous != None).
        topology_same = AgentTopology(
            kind=topology.kind,
            members=topology.members,
            comm_channels=topology.comm_channels,
            topology_id=topology.topology_id,  # same id
            created_at=topology.created_at,
        )
        adapter.set_topology(topology_same)
        change_events = [
            e for e in adapter.events if e.event_type == "topology_change"
        ]
        assert change_events == []

    def test_topology_is_sticky_across_subsequent_evaluations(self):
        """Once set, topology stays sticky — every subsequent
        attestation carries the topology Evidence."""
        adapter = _make_adapter()
        topology = _topology()
        adapter.set_topology(topology)

        # Drive a tool call — produces a new attestation.
        asyncio.new_event_loop().run_until_complete(
            adapter.on_event("pre_tool_use", {
                "tool_name": "calc", "tool_input": {}
            })
        )
        attestations = [
            r for r in adapter.attestation_chain.records
            if isinstance(r, AttestationRecord)
        ]
        # First attestation from set_topology, second from pre_tool_use.
        assert len(attestations) == 2
        for a in attestations:
            assert any(e.evidence_type == "topology" for e in a.evidence)
        # Only the FIRST attestation carries topology_change (none yet).
        assert all(
            e.evidence_type != "topology_change"
            for e in attestations[0].evidence
        )

    def test_pending_topology_change_consumed_after_one_attestation(self):
        """topology_change Evidence appears on exactly one attestation,
        not propagated forward indefinitely."""
        adapter = _make_adapter()
        topology1 = _topology()
        adapter.set_topology(topology1)

        new_member = AgentMember(
            agent_id="m-2", name="Worker B", role=AgentRole.MEMBER,
            parent_id="lead-1", capabilities=("tool_use",),
        )
        topology2 = topology1.with_member(new_member)
        adapter.set_topology(topology2)

        # Drive another evaluation.
        asyncio.new_event_loop().run_until_complete(
            adapter.on_event("pre_tool_use", {
                "tool_name": "calc", "tool_input": {}
            })
        )

        attestations = [
            r for r in adapter.attestation_chain.records
            if isinstance(r, AttestationRecord)
        ]
        # 3 attestations: declared, change, pre_tool_use
        assert len(attestations) == 3
        # change Evidence on attestation[1] only.
        ev1 = {e.evidence_type for e in attestations[1].evidence}
        assert "topology_change" in ev1
        ev2 = {e.evidence_type for e in attestations[2].evidence}
        assert "topology_change" not in ev2
        assert "topology" in ev2  # still sticky


class TestMultiAgentHandlers:
    def test_peer_message_buffered_and_emitted(self):
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "peer_message",
            {"sender_agent_id": "m-2", "content": "do as I say",
             "channel": "peer_messages"},
        ))

        assert len(adapter._buffer.peer_messages) == 1
        assert adapter._buffer.peer_messages[0]["sender_agent_id"] == "m-2"
        assert adapter._buffer.peer_messages[0]["content"] == "do as I say"

        events = [e for e in adapter.events if e.event_type == "peer_message"]
        assert len(events) == 1

    def test_shared_memory_write_captures_writer_agent_id(self):
        """T-SHARED-MEM-MISATTRIB mitigation."""
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "shared_memory_write",
            {"writer_agent_id": "compromised-peer", "key": "tasks",
             "content": "delete prod", "content_hash": "abc",
             "summary": "task list update"},
        ))

        assert len(adapter._buffer.shared_memory) == 1
        entry = adapter._buffer.shared_memory[0]
        assert entry["writer_agent_id"] == "compromised-peer"
        assert entry["key"] == "tasks"

    def test_broadcast_buffered(self):
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "broadcast",
            {"sender_agent_id": "broadcaster", "channel": "global",
             "content": "all agents stop"},
        ))
        assert len(adapter._buffer.broadcast_messages) == 1
        assert adapter._buffer.broadcast_messages[0]["sender_agent_id"] == "broadcaster"

    def test_broadcast_overflow_caps_at_1000(self):
        """T-BROADCAST-AMP mitigation: cap at 1000 entries."""
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        # Pre-fill the buffer.
        adapter._buffer.broadcast_messages.extend([{} for _ in range(1000)])
        loop.run_until_complete(adapter.on_event(
            "broadcast",
            {"sender_agent_id": "x", "channel": "y", "content": "z"},
        ))
        # Did NOT append the extra entry.
        assert len(adapter._buffer.broadcast_messages) == 1000
        # Emitted overflow event.
        overflow = [e for e in adapter.events if e.event_type == "broadcast_overflow"]
        assert len(overflow) == 1

    def test_task_started_buffered_separately_from_subagents(self):
        """Task is not a subagent — CrewAI semantic fix."""
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "task_started",
            {"task_id": "t1", "description": "summarize", "agent_id": "a1"},
        ))
        assert len(adapter._buffer.tasks) == 1
        # Tasks do NOT populate subagents.
        assert len(adapter._buffer.subagents) == 0

    def test_orphan_subagent_stop_logged_and_event_emitted(self):
        """T-ORPHAN-LIFECYCLE mitigation."""
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        # Send a stop with no matching start.
        loop.run_until_complete(adapter.on_event(
            "subagent_stop", {"agent_id": "orphan-1"},
        ))
        orphan_events = [e for e in adapter.events if e.event_type == "subagent_orphan"]
        assert len(orphan_events) == 1
        assert orphan_events[0].data["agent_id"] == "orphan-1"
        assert orphan_events[0].data["reason"] == "stop_without_start"

    def test_paired_subagent_start_stop_no_orphan(self):
        adapter = _make_adapter()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "subagent_start", {"agent_id": "child-1"},
        ))
        loop.run_until_complete(adapter.on_event(
            "subagent_stop", {"agent_id": "child-1"},
        ))
        orphan_events = [e for e in adapter.events if e.event_type == "subagent_orphan"]
        assert orphan_events == []


class TestToEvaluationContextMultiAgent:
    def test_topology_context_present_when_topology_set(self):
        adapter = _make_adapter()
        topology = _topology()
        adapter.set_topology(topology, my_role=AgentRole.LEADER)
        ctx = adapter._buffer.to_evaluation_context()
        assert "topology_context" in ctx
        tc = ctx["topology_context"]
        assert tc["topology"]["topology_id"] == topology.topology_id
        assert tc["role"] == "leader"

    def test_topology_context_absent_when_nothing_set(self):
        adapter = _make_adapter()
        ctx = adapter._buffer.to_evaluation_context()
        assert "topology_context" not in ctx

    def test_topology_context_includes_peer_messages(self):
        adapter = _make_adapter()
        adapter._buffer.peer_messages.append(
            {"sender_agent_id": "p1", "content": "hi"}
        )
        ctx = adapter._buffer.to_evaluation_context()
        assert "topology_context" in ctx
        assert len(ctx["topology_context"]["peer_messages"]) == 1

    def test_peer_messages_at_top_level_for_backward_compat(self):
        """AdversarialLayer reads context['peer_messages'] directly
        (line 554 of adversarial.py). Don't move it under
        topology_context only."""
        adapter = _make_adapter()
        adapter._buffer.peer_messages.append(
            {"sender_agent_id": "p1", "content": "hi"}
        )
        ctx = adapter._buffer.to_evaluation_context()
        assert "peer_messages" in ctx
        assert len(ctx["peer_messages"]) == 1


class TestVerifyCrossAgentLinksStub:
    """v0.8 stub returns True permissively (no peer chains supplied).
    Full implementation lands in v0.9 with KeyProvider."""

    def test_no_peer_chains_returns_true(self):
        chain = AttestationChain()
        chain.append(build_attestation_record(_profile(), _score()))
        assert chain.verify_cross_agent_links() is True

    def test_resolves_real_peer_evidence(self):
        peer_chain = AttestationChain()
        peer_record = build_attestation_record(_profile(), _score())
        peer_chain.append(peer_record)

        my_chain = AttestationChain()
        my_record = build_attestation_record(_profile(), _score())
        my_record.evidence.append(Evidence(
            evidence_type="peer_message",
            source=f"peer-1:{peer_record.record_id}",
            content_hash=peer_record.content_hash,
            summary="test peer message",
        ))
        my_chain.append(my_record)

        assert my_chain.verify_cross_agent_links({"peer-1": peer_chain}) is True

    def test_orphan_peer_evidence_fails(self):
        my_chain = AttestationChain()
        my_record = build_attestation_record(_profile(), _score())
        my_record.evidence.append(Evidence(
            evidence_type="peer_message",
            source="peer-1:nonexistent-record-id",
            content_hash="deadbeef" * 8,
            summary="phantom",
        ))
        my_chain.append(my_record)

        peer_chain = AttestationChain()
        peer_chain.append(build_attestation_record(_profile(), _score()))
        assert my_chain.verify_cross_agent_links({"peer-1": peer_chain}) is False

    def test_malformed_source_fails(self):
        my_chain = AttestationChain()
        my_record = build_attestation_record(_profile(), _score())
        my_record.evidence.append(Evidence(
            evidence_type="peer_message",
            source="no-colon",  # missing peer_id:record_id format
            content_hash="abc",
            summary="malformed",
        ))
        my_chain.append(my_record)
        assert my_chain.verify_cross_agent_links({"peer-1": AttestationChain()}) is False

    def test_tampered_peer_record_fails(self):
        peer_chain = AttestationChain()
        peer_record = build_attestation_record(_profile(), _score())
        peer_chain.append(peer_record)

        my_chain = AttestationChain()
        my_record = build_attestation_record(_profile(), _score())
        # Evidence committed to one hash; if peer record changes, the
        # hashes diverge.
        my_record.evidence.append(Evidence(
            evidence_type="peer_message",
            source=f"peer-1:{peer_record.record_id}",
            content_hash="wrong-hash",
            summary="mismatch",
        ))
        my_chain.append(my_record)
        assert my_chain.verify_cross_agent_links({"peer-1": peer_chain}) is False
