"""Tests for Phase 4b + 4c adapter uplifts: Bedrock, AutoGen, Google
ADK, LangChain (LangGraph), OpenAI Agents.

Each adapter declares an AgentTopology at the right discovery point
(instrument time for static; incremental for runtime-discovered) and
the chain commits to it via Evidence(evidence_type="topology")."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from agentegrity.core.attestation import AttestationRecord
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)
from agentegrity.core.topology import AgentRole, TopologyKind


def _profile():
    return AgentProfile(
        name="phase4bc",
        agent_type=AgentType.MULTI_AGENT,
        capabilities=["tool_use", "multi_agent_comm"],
        deployment_context=DeploymentContext.MULTI_AGENT,
        risk_tier=RiskTier.MEDIUM,
    )


class TestBedrockTopologySeed:
    def test_wrap_client_seeds_supervisor_topology(self):
        from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter

        # Fake boto3 client
        client = MagicMock()
        client.invoke_agent.return_value = {"completion": iter([])}

        adapter = BedrockAgentsAdapter(profile=_profile())
        wrapped = adapter.wrap_client(client)
        # Trigger invoke_agent
        wrapped.invoke_agent(agentId="supervisor-1", inputText="hello")

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.HUB_SPOKE
        assert topology.leader() is not None
        assert topology.leader().agent_id == "supervisor-1"

    def test_collaborator_observed_grows_topology(self):
        from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter

        adapter = BedrockAgentsAdapter(profile=_profile())
        adapter._seed_topology_from_supervisor("supervisor-x")
        adapter._ensure_collaborator("researcher")
        adapter._ensure_collaborator("writer")

        topology = adapter._buffer.topology
        assert topology is not None
        assert len(topology.members) == 3
        members_by_role: dict[AgentRole, set[str]] = {}
        for m in topology.members:
            members_by_role.setdefault(m.role, set()).add(m.agent_id)
        assert "supervisor-x" in members_by_role[AgentRole.LEADER]
        assert {"researcher", "writer"} <= members_by_role[AgentRole.MEMBER]

    def test_collaborator_idempotent(self):
        from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter

        adapter = BedrockAgentsAdapter(profile=_profile())
        adapter._seed_topology_from_supervisor("sup")
        adapter._ensure_collaborator("a")
        adapter._ensure_collaborator("a")  # second call no-op
        topology = adapter._buffer.topology
        assert len(topology.members) == 2  # supervisor + 1 unique collaborator


class TestAutoGenIncrementalTopology:
    def test_root_invoke_agent_seeds_group_chat(self):
        from agentegrity.adapters.autogen import AutoGenAdapter

        adapter = AutoGenAdapter(profile=_profile())
        adapter._seed_topology_for_root("agent-root", "Root")

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.GROUP_CHAT
        assert len(topology.members) == 1
        # GROUP_CHAT has no leader (PEER role)
        assert topology.leader() is None
        assert topology.members[0].role is AgentRole.PEER

    def test_nested_invoke_agent_appends_member(self):
        from agentegrity.adapters.autogen import AutoGenAdapter

        adapter = AutoGenAdapter(profile=_profile())
        adapter._seed_topology_for_root("agent-root", "Root")
        adapter._ensure_member("agent-1", "Agent One")
        adapter._ensure_member("agent-2", "Agent Two")

        topology = adapter._buffer.topology
        assert topology is not None
        assert len(topology.members) == 3
        ids = {m.agent_id for m in topology.members}
        assert ids == {"agent-root", "agent-1", "agent-2"}

    def test_ensure_member_seeds_if_no_root(self):
        """OTel sampling can drop the root span; nested span arrives
        first. Should lazily seed."""
        from agentegrity.adapters.autogen import AutoGenAdapter

        adapter = AutoGenAdapter(profile=_profile())
        # No root seed; nested arrives first
        adapter._ensure_member("late-agent", "Late")

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.GROUP_CHAT
        assert len(topology.members) == 1
        assert topology.members[0].agent_id == "late-agent"


class TestGoogleADKWorkflowTopology:
    def test_sequential_workflow_with_sub_agents_declares_topology(self):
        from agentegrity.adapters.google_adk import GoogleADKAdapter

        adapter = GoogleADKAdapter(profile=_profile())

        # Fake SequentialAgent with sub_agents
        fake_agent = MagicMock()
        fake_agent.name = "researcher_workflow"
        sub1 = MagicMock()
        sub1.name = "fetch"
        sub2 = MagicMock()
        sub2.name = "summarize"
        fake_agent.sub_agents = [sub1, sub2]
        # Make the callback attributes settable
        fake_agent.before_agent_callback = None
        fake_agent.after_agent_callback = None
        fake_agent.before_tool_callback = None
        fake_agent.after_tool_callback = None

        adapter.instrument(fake_agent)

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.HIERARCHICAL_DAG
        assert len(topology.members) == 3  # 1 supervisor + 2 workers
        roles = {m.role for m in topology.members}
        assert roles == {AgentRole.SUPERVISOR, AgentRole.WORKER}

    def test_plain_agent_without_sub_agents_no_topology(self):
        from agentegrity.adapters.google_adk import GoogleADKAdapter

        adapter = GoogleADKAdapter(profile=_profile())
        fake_agent = MagicMock(spec=[
            "before_agent_callback", "after_agent_callback",
            "before_tool_callback", "after_tool_callback",
        ])
        fake_agent.before_agent_callback = None
        fake_agent.after_agent_callback = None
        fake_agent.before_tool_callback = None
        fake_agent.after_tool_callback = None
        # Explicitly no sub_agents attribute
        adapter.instrument(fake_agent)

        # Plain Agent is correctly single-agent — no topology.
        assert adapter._buffer.topology is None


class TestLangGraphTopologyIntrospection:
    def test_supervisor_pattern_declares_hierarchical_dag(self):
        from agentegrity.adapters.langchain import LangChainAdapter

        adapter = LangChainAdapter(profile=_profile())

        # Fake compiled LangGraph with get_graph()
        graph_obj = MagicMock()
        graph_obj.nodes = {
            "supervisor": {},
            "researcher": {},
            "writer": {},
            "__start__": {},
            "__end__": {},
        }
        fake_graph = MagicMock()
        fake_graph.get_graph.return_value = graph_obj
        # Required by instrument_chain (with_config)
        fake_graph.with_config.return_value = fake_graph

        adapter.instrument_graph(fake_graph)

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.HIERARCHICAL_DAG
        assert len(topology.members) == 3  # supervisor + 2 workers
        members_by_role: dict[AgentRole, set[str]] = {}
        for m in topology.members:
            members_by_role.setdefault(m.role, set()).add(m.agent_id)
        assert "supervisor" in members_by_role[AgentRole.SUPERVISOR]
        assert {"researcher", "writer"} == members_by_role[AgentRole.WORKER]

    def test_swarm_pattern_declares_peer_to_peer(self):
        from agentegrity.adapters.langchain import LangChainAdapter

        adapter = LangChainAdapter(profile=_profile())

        graph_obj = MagicMock()
        graph_obj.nodes = {
            "researcher": {},
            "analyst": {},
            "writer": {},
        }
        fake_graph = MagicMock()
        fake_graph.get_graph.return_value = graph_obj
        fake_graph.with_config.return_value = fake_graph

        adapter.instrument_graph(fake_graph)

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.PEER_TO_PEER
        assert len(topology.members) == 3
        # All peers
        assert all(m.role is AgentRole.PEER for m in topology.members)

    def test_graph_introspection_failure_falls_back(self):
        """If get_graph fails / is absent, single-agent fallback."""
        from agentegrity.adapters.langchain import LangChainAdapter

        adapter = LangChainAdapter(profile=_profile())

        # No get_graph method at all
        fake_graph = MagicMock(spec=["with_config"])
        fake_graph.with_config.return_value = fake_graph

        adapter.instrument_graph(fake_graph)
        # No topology declared.
        assert adapter._buffer.topology is None


class TestOpenAIAgentsHandoffTopology:
    def test_initial_agent_seeds_peer_to_peer_topology(self):
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile())
        adapter._seed_topology_from_initial("agent-alpha")

        topology = adapter._buffer.topology
        assert topology is not None
        assert topology.kind is TopologyKind.PEER_TO_PEER
        assert len(topology.members) == 1
        assert topology.members[0].agent_id == "agent-alpha"
        assert topology.members[0].role is AgentRole.PEER

    def test_handoff_grows_topology_incrementally(self):
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile())
        adapter._seed_topology_from_initial("alpha")
        adapter._add_handoff_target("beta")
        adapter._add_handoff_target("gamma")

        topology = adapter._buffer.topology
        assert topology is not None
        ids = {m.agent_id for m in topology.members}
        assert ids == {"alpha", "beta", "gamma"}

    def test_handoff_evidence_on_attestation(self):
        """Each topology_change after a handoff triggers an attestation
        carrying Evidence(evidence_type='topology_change')."""
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile())
        adapter._seed_topology_from_initial("alpha")
        adapter._add_handoff_target("beta")

        attestations = [
            r for r in adapter.attestation_chain.records
            if isinstance(r, AttestationRecord)
        ]
        # Two attestations: seed + handoff
        assert len(attestations) >= 2
        ev_types_last = {e.evidence_type for e in attestations[-1].evidence}
        assert "topology" in ev_types_last
        assert "topology_change" in ev_types_last


class TestClaudeSDKSingleAgentByDesign:
    """Claude Agent SDK is single-agent by framework design. The
    adapter should NOT declare any topology — that's correct, not a
    gap. This test pins the absence so future refactors don't
    accidentally regress it."""

    def test_claude_adapter_does_not_declare_topology(self):
        from agentegrity.adapters.claude import ClaudeAdapter

        adapter = ClaudeAdapter(profile=_profile())

        # Drive the canonical event stream — should NEVER touch topology.
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.on_event(
            "user_prompt_submit", {"prompt": "hello"}
        ))
        loop.run_until_complete(adapter.on_event(
            "pre_tool_use", {"tool_name": "calc", "tool_input": {}}
        ))
        loop.run_until_complete(adapter.on_event(
            "stop", {"output": "done"}
        ))

        assert adapter._buffer.topology is None
