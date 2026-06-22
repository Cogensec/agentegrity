"""Tests for AgentTopology, AgentMember, TopologyChange (Phase 1)."""

import subprocess
import sys
import textwrap

import pytest

from agentegrity.core.topology import (
    AgentMember,
    AgentRole,
    AgentTopology,
    TopologyChange,
    TopologyKind,
)


def make_member(agent_id="agent-1", role=AgentRole.MEMBER, parent_id=None):
    return AgentMember(
        agent_id=agent_id,
        name=f"Agent {agent_id}",
        role=role,
        parent_id=parent_id,
        capabilities=("tool_use",),
    )


def make_hub_spoke():
    leader = make_member(agent_id="lead-1", role=AgentRole.LEADER)
    member1 = make_member(agent_id="m-1", role=AgentRole.MEMBER, parent_id="lead-1")
    member2 = make_member(agent_id="m-2", role=AgentRole.MEMBER, parent_id="lead-1")
    return AgentTopology(
        kind=TopologyKind.HUB_SPOKE,
        members=(leader, member1, member2),
        comm_channels=frozenset({"peer_messages"}),
    )


class TestAgentMember:
    def test_construction(self):
        m = make_member()
        assert m.agent_id == "agent-1"
        assert m.role is AgentRole.MEMBER
        assert m.capabilities == ("tool_use",)

    def test_frozen_dataclass_rejects_mutation(self):
        m = make_member()
        with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError or AttributeError
            m.agent_id = "different"  # type: ignore[misc]

    def test_round_trip(self):
        m = make_member(agent_id="x", role=AgentRole.SUPERVISOR, parent_id="root")
        d = m.to_dict()
        rebuilt = AgentMember.from_dict(d)
        assert rebuilt == m

    def test_hashable(self):
        m1 = make_member()
        m2 = make_member()
        # Same fields → same hash; frozen dataclass is hashable.
        assert hash(m1) == hash(m2)
        assert {m1, m2} == {m1}


class TestAgentTopology:
    def test_hub_spoke_construction(self):
        t = make_hub_spoke()
        assert t.kind is TopologyKind.HUB_SPOKE
        assert len(t.members) == 3
        assert "peer_messages" in t.comm_channels

    def test_leader_lookup(self):
        t = make_hub_spoke()
        leader = t.leader()
        assert leader is not None
        assert leader.agent_id == "lead-1"

    def test_leader_none_for_peer_to_peer(self):
        t = AgentTopology(
            kind=TopologyKind.PEER_TO_PEER,
            members=(
                make_member(agent_id="p-1", role=AgentRole.PEER),
                make_member(agent_id="p-2", role=AgentRole.PEER),
            ),
        )
        assert t.leader() is None

    def test_children_of(self):
        t = make_hub_spoke()
        children = t.children_of("lead-1")
        assert {c.agent_id for c in children} == {"m-1", "m-2"}
        assert t.children_of("nonexistent") == ()

    def test_member_lookup(self):
        t = make_hub_spoke()
        assert t.member("m-1") is not None
        assert t.member("m-1").role is AgentRole.MEMBER
        assert t.member("nonexistent") is None

    def test_content_hash_deterministic_in_process(self):
        t1 = make_hub_spoke()
        t2 = make_hub_spoke()
        # topology_id and created_at differ per construction, but
        # content_hash is structural — it should match for identical
        # kind + members + channels.
        assert t1.content_hash() == t2.content_hash()

    def test_content_hash_differs_for_different_structure(self):
        t1 = make_hub_spoke()
        t2 = t1.with_member(make_member(agent_id="m-3", role=AgentRole.MEMBER, parent_id="lead-1"))
        assert t1.content_hash() != t2.content_hash()

    def test_content_hash_deterministic_across_processes(self):
        """Content hash must survive process boundaries for tamper
        detection on serialized chains."""
        script = textwrap.dedent("""
            from agentegrity.core.topology import (
                AgentMember, AgentRole, AgentTopology, TopologyKind,
            )
            members = (
                AgentMember(agent_id="lead-1", name="Agent lead-1",
                            role=AgentRole.LEADER,
                            capabilities=("tool_use",)),
                AgentMember(agent_id="m-1", name="Agent m-1",
                            role=AgentRole.MEMBER, parent_id="lead-1",
                            capabilities=("tool_use",)),
            )
            t = AgentTopology(
                kind=TopologyKind.HUB_SPOKE,
                members=members,
                comm_channels=frozenset({"peer_messages"}),
            )
            print(t.content_hash())
        """)
        out1 = subprocess.check_output(
            [sys.executable, "-c", script], text=True
        ).strip()
        out2 = subprocess.check_output(
            [sys.executable, "-c", script], text=True
        ).strip()
        assert out1 == out2
        assert len(out1) == 64

    def test_frozen_dataclass_rejects_mutation(self):
        t = make_hub_spoke()
        with pytest.raises(Exception):  # noqa: B017
            t.members = ()  # type: ignore[misc]

    def test_with_member_returns_new_snapshot(self):
        t = make_hub_spoke()
        original_hash = t.content_hash()
        new_member = make_member(agent_id="m-3", role=AgentRole.MEMBER, parent_id="lead-1")
        t2 = t.with_member(new_member)
        # Old snapshot unchanged
        assert t.content_hash() == original_hash
        assert len(t.members) == 3
        # New snapshot has the addition
        assert len(t2.members) == 4
        assert t2.member("m-3") is not None
        # topology_id is preserved (same logical topology)
        assert t.topology_id == t2.topology_id

    def test_with_member_replaces_same_id(self):
        t = make_hub_spoke()
        updated = make_member(agent_id="m-1", role=AgentRole.SUPERVISOR, parent_id="lead-1")
        t2 = t.with_member(updated)
        assert len(t2.members) == 3
        assert t2.member("m-1").role is AgentRole.SUPERVISOR

    def test_without_member_returns_new_snapshot(self):
        t = make_hub_spoke()
        t2 = t.without_member("m-2")
        assert len(t.members) == 3  # original unchanged
        assert len(t2.members) == 2
        assert t2.member("m-2") is None

    def test_with_channels(self):
        t = make_hub_spoke()
        t2 = t.with_channels("shared_memory", "broadcast_channels")
        assert "peer_messages" in t2.comm_channels
        assert "shared_memory" in t2.comm_channels
        assert "broadcast_channels" in t2.comm_channels
        # Original unchanged
        assert "shared_memory" not in t.comm_channels

    def test_round_trip(self):
        t = make_hub_spoke()
        d = t.to_dict()
        rebuilt = AgentTopology.from_dict(d)
        assert rebuilt.kind == t.kind
        assert rebuilt.topology_id == t.topology_id
        assert rebuilt.members == t.members
        assert rebuilt.comm_channels == t.comm_channels
        # content_hash matches
        assert rebuilt.content_hash() == t.content_hash()

    def test_to_dict_includes_content_hash(self):
        t = make_hub_spoke()
        d = t.to_dict()
        assert d["content_hash"] == t.content_hash()
        assert d["kind"] == "hub_spoke"
        assert len(d["members"]) == 3


class TestTopologyChange:
    def test_diff_addition(self):
        t1 = make_hub_spoke()
        new_member = make_member(agent_id="m-3", role=AgentRole.MEMBER, parent_id="lead-1")
        t2 = t1.with_member(new_member)
        change = TopologyChange.between(t1, t2)
        assert change.previous_topology_id == t1.topology_id
        assert change.previous_content_hash == t1.content_hash()
        assert change.new_content_hash == t2.content_hash()
        assert change.added_members == (new_member,)
        assert change.removed_member_ids == ()

    def test_diff_removal(self):
        t1 = make_hub_spoke()
        t2 = t1.without_member("m-2")
        change = TopologyChange.between(t1, t2)
        assert change.added_members == ()
        assert change.removed_member_ids == ("m-2",)

    def test_round_trip(self):
        t1 = make_hub_spoke()
        t2 = t1.with_member(make_member(agent_id="m-3", role=AgentRole.MEMBER, parent_id="lead-1"))
        change = TopologyChange.between(t1, t2)
        d = change.to_dict()
        assert d["previous_topology_id"] == t1.topology_id
        assert d["added_members"][0]["agent_id"] == "m-3"


class TestDynamicTeamSequence:
    """A dynamic team (e.g. Agno's callable members provider) is
    represented as a sequence of immutable snapshots, each linked by
    TopologyChange."""

    def test_member_addition_sequence(self):
        t0 = AgentTopology(
            kind=TopologyKind.HUB_SPOKE,
            members=(make_member(agent_id="lead-1", role=AgentRole.LEADER),),
        )
        t1 = t0.with_member(make_member(agent_id="m-1", role=AgentRole.MEMBER, parent_id="lead-1"))
        t2 = t1.with_member(make_member(agent_id="m-2", role=AgentRole.MEMBER, parent_id="lead-1"))

        # Each snapshot has a distinct content hash
        hashes = [t0.content_hash(), t1.content_hash(), t2.content_hash()]
        assert len(set(hashes)) == 3
        # But all share the topology_id (same logical topology)
        assert t0.topology_id == t1.topology_id == t2.topology_id

        # Sequential diffs link them
        change1 = TopologyChange.between(t0, t1)
        change2 = TopologyChange.between(t1, t2)
        assert change1.new_content_hash == change2.previous_content_hash

    def test_group_chat_no_leader(self):
        t = AgentTopology(
            kind=TopologyKind.GROUP_CHAT,
            members=(
                make_member(agent_id="a1", role=AgentRole.PEER),
                make_member(agent_id="a2", role=AgentRole.PEER),
                make_member(agent_id="a3", role=AgentRole.PEER),
            ),
            comm_channels=frozenset({"broadcast_channels"}),
        )
        assert t.leader() is None
        # No hierarchical relationships
        for m in t.members:
            assert m.parent_id is None
            assert t.children_of(m.agent_id) == ()
