"""
Agent topology - the in-process multi-agent system snapshot.

Agentegrity models multi-agent systems as immutable topologies.
Each :class:`AgentTopology` is a hashable snapshot of which agents
participate, what roles they hold, and which communication channels
exist between them. Mutation produces a new snapshot with a new
content hash; the old snapshot is unchanged.

Immutability is load-bearing for two invariants:

1. Recovery integrity needs deterministic restore targets. A mutable
   topology means "restore to checkpoint X" is undefined when the
   topology since changed. Snapshot-and-replace makes the restore
   target an explicit object.
2. Attestation records commit to *which* topology was live at
   evaluation time. The attestation carries a reference (Evidence of
   type ``"topology"``) whose content hash pins the snapshot.

The :class:`TopologyKind` enum names the four shapes the supported
adapters expose: hub-spoke (CrewAI sequential, Agno coordinator,
Bedrock supervisor), hierarchical DAG (LangGraph workflows, Google
ADK), peer-to-peer (LangGraph swarm, OpenAI Swarm, Agno
collaborator), and group chat (AutoGen). Adapters declare their
topology kind at instrument time and the layers consume it through
``_ContextBuffer.to_evaluation_context()``.

A dynamic team (e.g. Agno's callable members provider) is
represented as a sequence of topology snapshots; each membership
change emits a ``topology_change`` event and a fresh
:class:`AgentTopology` lands in the chain.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AgentRole(str, Enum):
    """The role an agent holds within its topology.

    Naming is deliberately framework-agnostic. Hub-spoke topologies
    use ``LEADER`` for the orchestrator and ``MEMBER`` for the
    workers. Hierarchical DAGs use ``SUPERVISOR`` and ``WORKER``.
    Peer-to-peer topologies use ``PEER`` throughout.

    The role drives the Cortical layer's role-conformance check
    (the same agent in different roles gets different behavioural
    baselines) and the Federation layer's topology-coherence score
    (LEADER tool-call frequency vs WORKER tool-call frequency).
    """

    LEADER = "leader"
    MEMBER = "member"
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    PEER = "peer"


class TopologyKind(str, Enum):
    """The shape of a multi-agent topology.

    Maps onto the framework landscape:

    * ``HUB_SPOKE``: one orchestrator + N workers. CrewAI sequential
      crews, Agno teams in coordinator mode, AWS Bedrock supervisor
      with collaborators.
    * ``HIERARCHICAL_DAG``: arbitrary parent-child tree.
      LangGraph workflow graphs, Google ADK ``SequentialAgent`` /
      ``ParallelAgent`` / ``LoopAgent`` compositions.
    * ``PEER_TO_PEER``: no orchestrator, handoffs between equals.
      LangGraph swarm, OpenAI Swarm, Agno collaborator mode.
    * ``GROUP_CHAT``: dynamic membership with broadcast comms.
      AutoGen ``GroupChatManager``-driven conversations.
    """

    HUB_SPOKE = "hub_spoke"
    HIERARCHICAL_DAG = "hierarchical_dag"
    PEER_TO_PEER = "peer_to_peer"
    GROUP_CHAT = "group_chat"


@dataclass(frozen=True)
class AgentMember:
    """One agent's place in a topology snapshot.

    ``capabilities`` is a tuple (not a list) so the dataclass is
    hashable. ``parent_id`` is the immediate parent's ``agent_id``
    or ``None`` for the topology's root (the leader in HUB_SPOKE,
    the supervisor of a HIERARCHICAL_DAG, etc.). In PEER_TO_PEER
    and GROUP_CHAT topologies every member has ``parent_id=None``.
    """

    agent_id: str
    name: str
    role: AgentRole
    parent_id: str | None = None
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "parent_id": self.parent_id,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentMember":
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            role=AgentRole(data["role"]),
            parent_id=data.get("parent_id"),
            capabilities=tuple(data.get("capabilities", ())),
        )


@dataclass(frozen=True)
class AgentTopology:
    """An immutable snapshot of a multi-agent topology.

    Constructed once at instrument time; mutations produce a new
    snapshot via :meth:`with_member` / :meth:`without_member`.
    ``content_hash()`` returns a deterministic SHA-256 over the
    canonical JSON; this hash is what attestations commit to via
    ``Evidence(evidence_type="topology", ...)``.

    ``topology_id`` is generated at construction and stays stable
    across mutations of the same logical topology, so a verifier
    can correlate a sequence of snapshots. ``content_hash`` differs
    on every mutation.
    """

    kind: TopologyKind
    members: tuple[AgentMember, ...] = ()
    comm_channels: frozenset[str] = field(default_factory=frozenset)
    topology_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def leader(self) -> AgentMember | None:
        """The orchestrator member, if one exists.

        Hub-spoke and hierarchical-DAG topologies have one;
        peer-to-peer and group-chat return ``None``.
        """
        for member in self.members:
            if member.role in (AgentRole.LEADER, AgentRole.SUPERVISOR):
                return member
        return None

    def children_of(self, agent_id: str) -> tuple[AgentMember, ...]:
        """Members whose ``parent_id`` equals ``agent_id``."""
        return tuple(m for m in self.members if m.parent_id == agent_id)

    def member(self, agent_id: str) -> AgentMember | None:
        """Look up a member by ``agent_id``."""
        for m in self.members:
            if m.agent_id == agent_id:
                return m
        return None

    @property
    def canonical_payload(self) -> str:
        """Deterministic JSON serialization used for content hashing.

        ``topology_id`` and ``created_at`` are NOT in the payload —
        the hash captures the structural shape (kind, members,
        channels), so adding the same member twice in two different
        sessions produces the same hash. Use ``topology_id`` to
        correlate the lineage; use ``content_hash`` to identify the
        structure.
        """
        payload = {
            "kind": self.kind.value,
            "members": [m.to_dict() for m in self.members],
            "comm_channels": sorted(self.comm_channels),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def content_hash(self) -> str:
        """SHA-256 of the canonical payload."""
        return hashlib.sha256(self.canonical_payload.encode()).hexdigest()

    def with_member(self, member: AgentMember) -> "AgentTopology":
        """Return a new snapshot with ``member`` added.

        Preserves ``topology_id`` (same logical topology) but
        produces a fresh ``content_hash``. If a member with the
        same ``agent_id`` already exists, it is replaced.
        """
        existing = tuple(m for m in self.members if m.agent_id != member.agent_id)
        return replace(self, members=existing + (member,))

    def without_member(self, agent_id: str) -> "AgentTopology":
        """Return a new snapshot with the member removed."""
        return replace(
            self,
            members=tuple(m for m in self.members if m.agent_id != agent_id),
        )

    def with_channels(self, *channels: str) -> "AgentTopology":
        """Return a new snapshot with additional comm channels."""
        return replace(self, comm_channels=self.comm_channels | frozenset(channels))

    def to_dict(self) -> dict[str, Any]:
        return {
            "topology_id": self.topology_id,
            "kind": self.kind.value,
            "members": [m.to_dict() for m in self.members],
            "comm_channels": sorted(self.comm_channels),
            "created_at": self.created_at.isoformat(),
            "content_hash": self.content_hash(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTopology":
        """Rebuild a topology from its ``to_dict`` representation.

        ``content_hash`` in the input is ignored — it's derived from
        the canonical payload on demand. ``created_at`` is parsed
        from ISO format; if absent (older serializations), defaults
        to the current time.
        """
        created_at = (
            datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(timezone.utc)
        )
        return cls(
            topology_id=data.get("topology_id", str(uuid.uuid4())),
            kind=TopologyKind(data["kind"]),
            members=tuple(AgentMember.from_dict(m) for m in data.get("members", [])),
            comm_channels=frozenset(data.get("comm_channels", [])),
            created_at=created_at,
        )

    def __repr__(self) -> str:
        return (
            f"AgentTopology(kind={self.kind.value}, "
            f"members={len(self.members)}, "
            f"channels={len(self.comm_channels)}, "
            f"id={self.topology_id[:8]}...)"
        )


@dataclass(frozen=True)
class TopologyChange:
    """A structural diff between two topology snapshots.

    Emitted by adapters when the topology mutates at runtime (e.g.
    Agno's callable members provider produces a new member; AutoGen
    sees a new nested span). The change is recorded in the chain as
    ``Evidence(evidence_type="topology_change", ...)`` so a verifier
    can walk the history of structural shifts.
    """

    previous_topology_id: str
    previous_content_hash: str
    new_topology_id: str
    new_content_hash: str
    added_members: tuple[AgentMember, ...] = ()
    removed_member_ids: tuple[str, ...] = ()

    @classmethod
    def between(
        cls, previous: AgentTopology, current: AgentTopology
    ) -> "TopologyChange":
        """Compute the diff from ``previous`` to ``current``."""
        prev_ids = {m.agent_id for m in previous.members}
        curr_ids = {m.agent_id for m in current.members}
        added = tuple(m for m in current.members if m.agent_id not in prev_ids)
        removed = tuple(prev_ids - curr_ids)
        return cls(
            previous_topology_id=previous.topology_id,
            previous_content_hash=previous.content_hash(),
            new_topology_id=current.topology_id,
            new_content_hash=current.content_hash(),
            added_members=added,
            removed_member_ids=removed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_topology_id": self.previous_topology_id,
            "previous_content_hash": self.previous_content_hash,
            "new_topology_id": self.new_topology_id,
            "new_content_hash": self.new_content_hash,
            "added_members": [m.to_dict() for m in self.added_members],
            "removed_member_ids": list(self.removed_member_ids),
        }
