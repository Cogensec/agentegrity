/**
 * `AgentTopology` — TypeScript mirror of Python's
 * `agentegrity.core.topology.AgentTopology`. Immutable snapshot of an
 * in-process multi-agent system.
 *
 * Mutations produce new snapshots via `withMember` / `withoutMember` /
 * `withChannels`. `contentHash()` returns a deterministic SHA-256 hex
 * digest of the canonical JSON — matches the Python implementation
 * exactly so a TS-produced topology snapshot Evidence references the
 * same hash a Python verifier would compute over the equivalent
 * topology.
 *
 * The TS Adapter (`DefaultAdapter`) declares a topology via
 * `setTopology(topology, myRole?)`; the first call emits a
 * `topology_declared` event, subsequent calls with a structurally-
 * distinct topology emit `topology_change`.
 */

import { createHash, randomUUID } from "node:crypto";

export enum AgentRole {
  LEADER = "leader",
  MEMBER = "member",
  SUPERVISOR = "supervisor",
  WORKER = "worker",
  PEER = "peer",
}

export enum TopologyKind {
  HUB_SPOKE = "hub_spoke",
  HIERARCHICAL_DAG = "hierarchical_dag",
  PEER_TO_PEER = "peer_to_peer",
  GROUP_CHAT = "group_chat",
}

export interface AgentMemberInit {
  agentId: string;
  name: string;
  role: AgentRole;
  parentId?: string | null;
  capabilities?: readonly string[];
}

/**
 * One agent's place in a topology snapshot. Immutable: constructed
 * once, frozen, never mutated. Use {@link AgentTopology.withMember}
 * to derive a new topology with this member added or replaced.
 */
export class AgentMember {
  readonly agentId: string;
  readonly name: string;
  readonly role: AgentRole;
  readonly parentId: string | null;
  readonly capabilities: readonly string[];

  constructor(init: AgentMemberInit) {
    this.agentId = init.agentId;
    this.name = init.name;
    this.role = init.role;
    this.parentId = init.parentId ?? null;
    this.capabilities = Object.freeze([...(init.capabilities ?? [])]);
    Object.freeze(this);
  }

  toDict(): Record<string, unknown> {
    return {
      agent_id: this.agentId,
      name: this.name,
      role: this.role,
      parent_id: this.parentId,
      capabilities: [...this.capabilities],
    };
  }

  static fromDict(data: Record<string, unknown>): AgentMember {
    return new AgentMember({
      agentId: String(data.agent_id),
      name: String(data.name),
      role: data.role as AgentRole,
      parentId: (data.parent_id as string | null | undefined) ?? null,
      capabilities: (data.capabilities as string[] | undefined) ?? [],
    });
  }
}

export interface AgentTopologyInit {
  kind: TopologyKind;
  members?: readonly AgentMember[];
  commChannels?: ReadonlySet<string> | readonly string[];
  topologyId?: string;
  createdAt?: string;
}

/**
 * Immutable in-process multi-agent topology snapshot. Mutations
 * produce new snapshots; the old snapshot is unchanged.
 *
 * Immutability is load-bearing for two invariants Python's
 * `AgentTopology` documents:
 * 1. Recovery integrity needs deterministic restore targets; a
 *    mutable topology makes "restore to checkpoint X" undefined.
 * 2. Attestations commit to *which* topology was live at
 *    evaluation time via Evidence(evidence_type="topology",
 *    content_hash=topology.contentHash()).
 */
export class AgentTopology {
  readonly kind: TopologyKind;
  readonly members: readonly AgentMember[];
  readonly commChannels: ReadonlySet<string>;
  readonly topologyId: string;
  readonly createdAt: string;

  constructor(init: AgentTopologyInit) {
    this.kind = init.kind;
    this.members = Object.freeze([...(init.members ?? [])]);
    this.commChannels = Object.freeze(
      new Set(
        init.commChannels instanceof Set
          ? [...init.commChannels]
          : [...(init.commChannels ?? [])],
      ),
    );
    this.topologyId = init.topologyId ?? randomUUID();
    this.createdAt = init.createdAt ?? new Date().toISOString();
    Object.freeze(this);
  }

  /** The orchestrator member, if one exists (HUB_SPOKE / HIERARCHICAL_DAG). */
  leader(): AgentMember | null {
    for (const m of this.members) {
      if (m.role === AgentRole.LEADER || m.role === AgentRole.SUPERVISOR) {
        return m;
      }
    }
    return null;
  }

  /** Members whose parentId equals agentId. */
  childrenOf(agentId: string): readonly AgentMember[] {
    return this.members.filter((m) => m.parentId === agentId);
  }

  /** Look up a member by agentId. */
  member(agentId: string): AgentMember | null {
    for (const m of this.members) {
      if (m.agentId === agentId) return m;
    }
    return null;
  }

  /**
   * Deterministic JSON serialization used for content hashing. The
   * topologyId and createdAt are NOT included — the hash captures
   * the structural shape (kind, members, channels) so two sessions
   * producing the same topology structurally produce the same hash.
   * Matches Python `AgentTopology.canonical_payload`.
   */
  canonicalPayload(): string {
    const payload = {
      kind: this.kind,
      members: this.members.map((m) => m.toDict()),
      comm_channels: [...this.commChannels].sort(),
    };
    return canonicalJsonStringify(payload);
  }

  /** SHA-256 hex digest of the canonical payload. */
  contentHash(): string {
    return createHash("sha256")
      .update(this.canonicalPayload())
      .digest("hex");
  }

  /**
   * Return a new snapshot with `member` added. Preserves topologyId
   * (same logical topology) but produces a fresh content_hash. If a
   * member with the same agentId already exists, it is replaced.
   */
  withMember(member: AgentMember): AgentTopology {
    const filtered = this.members.filter(
      (m) => m.agentId !== member.agentId,
    );
    return new AgentTopology({
      kind: this.kind,
      members: [...filtered, member],
      commChannels: this.commChannels,
      topologyId: this.topologyId,
      createdAt: this.createdAt,
    });
  }

  withoutMember(agentId: string): AgentTopology {
    return new AgentTopology({
      kind: this.kind,
      members: this.members.filter((m) => m.agentId !== agentId),
      commChannels: this.commChannels,
      topologyId: this.topologyId,
      createdAt: this.createdAt,
    });
  }

  withChannels(...channels: string[]): AgentTopology {
    const merged = new Set(this.commChannels);
    for (const c of channels) merged.add(c);
    return new AgentTopology({
      kind: this.kind,
      members: this.members,
      commChannels: merged,
      topologyId: this.topologyId,
      createdAt: this.createdAt,
    });
  }

  toDict(): Record<string, unknown> {
    return {
      topology_id: this.topologyId,
      kind: this.kind,
      members: this.members.map((m) => m.toDict()),
      comm_channels: [...this.commChannels].sort(),
      created_at: this.createdAt,
      content_hash: this.contentHash(),
    };
  }

  static fromDict(data: Record<string, unknown>): AgentTopology {
    return new AgentTopology({
      kind: data.kind as TopologyKind,
      members: ((data.members as Record<string, unknown>[]) ?? []).map(
        AgentMember.fromDict,
      ),
      commChannels: new Set(
        (data.comm_channels as string[] | undefined) ?? [],
      ),
      topologyId: (data.topology_id as string | undefined) ?? randomUUID(),
      createdAt:
        (data.created_at as string | undefined) ?? new Date().toISOString(),
    });
  }
}

export interface TopologyChangeData {
  previousTopologyId: string;
  previousContentHash: string;
  newTopologyId: string;
  newContentHash: string;
  addedMembers: readonly AgentMember[];
  removedMemberIds: readonly string[];
}

/**
 * A structural diff between two topology snapshots. Emitted when
 * the topology mutates at runtime. Carried on the chain as
 * Evidence(evidence_type="topology_change").
 */
export class TopologyChange {
  readonly previousTopologyId: string;
  readonly previousContentHash: string;
  readonly newTopologyId: string;
  readonly newContentHash: string;
  readonly addedMembers: readonly AgentMember[];
  readonly removedMemberIds: readonly string[];

  constructor(data: TopologyChangeData) {
    this.previousTopologyId = data.previousTopologyId;
    this.previousContentHash = data.previousContentHash;
    this.newTopologyId = data.newTopologyId;
    this.newContentHash = data.newContentHash;
    this.addedMembers = Object.freeze([...data.addedMembers]);
    this.removedMemberIds = Object.freeze([...data.removedMemberIds]);
    Object.freeze(this);
  }

  static between(
    previous: AgentTopology,
    current: AgentTopology,
  ): TopologyChange {
    const prevIds = new Set(previous.members.map((m) => m.agentId));
    const currIds = new Set(current.members.map((m) => m.agentId));
    const added = current.members.filter((m) => !prevIds.has(m.agentId));
    const removed = [...prevIds].filter((id) => !currIds.has(id));
    return new TopologyChange({
      previousTopologyId: previous.topologyId,
      previousContentHash: previous.contentHash(),
      newTopologyId: current.topologyId,
      newContentHash: current.contentHash(),
      addedMembers: added,
      removedMemberIds: removed,
    });
  }

  toDict(): Record<string, unknown> {
    return {
      previous_topology_id: this.previousTopologyId,
      previous_content_hash: this.previousContentHash,
      new_topology_id: this.newTopologyId,
      new_content_hash: this.newContentHash,
      added_members: this.addedMembers.map((m) => m.toDict()),
      removed_member_ids: [...this.removedMemberIds],
    };
  }
}

/**
 * Canonical JSON: deterministic key ordering (sorted), no whitespace.
 * Matches Python's `json.dumps(..., sort_keys=True, separators=(",",":"))`.
 *
 * Standard `JSON.stringify` doesn't sort keys; this helper does the
 * walk-and-sort manually.
 */
function canonicalJsonStringify(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "null";
    return String(value);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return "[" + value.map(canonicalJsonStringify).join(",") + "]";
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    entries.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    return (
      "{" +
      entries
        .map(([k, v]) => JSON.stringify(k) + ":" + canonicalJsonStringify(v))
        .join(",") +
      "}"
    );
  }
  // Fallback for undefined / symbol / function — match JSON.stringify
  // behavior (omit the key) by returning null; caller responsible for
  // not passing these in.
  return "null";
}
