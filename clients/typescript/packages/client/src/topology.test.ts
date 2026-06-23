/**
 * Tests for `AgentTopology`, `AgentMember`, `TopologyChange`.
 * Mirrors Python's `tests/test_topology.py`.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  AgentMember,
  AgentRole,
  AgentTopology,
  TopologyChange,
  TopologyKind,
} from "./topology.js";

function makeMember(
  agentId = "agent-1",
  role: AgentRole = AgentRole.MEMBER,
  parentId: string | null = null,
): AgentMember {
  return new AgentMember({
    agentId,
    name: `Agent ${agentId}`,
    role,
    parentId,
    capabilities: ["tool_use"],
  });
}

function makeHubSpoke(): AgentTopology {
  const leader = makeMember("lead-1", AgentRole.LEADER);
  const m1 = makeMember("m-1", AgentRole.MEMBER, "lead-1");
  const m2 = makeMember("m-2", AgentRole.MEMBER, "lead-1");
  return new AgentTopology({
    kind: TopologyKind.HUB_SPOKE,
    members: [leader, m1, m2],
    commChannels: new Set(["peer_messages"]),
  });
}

describe("AgentMember", () => {
  it("construction", () => {
    const m = makeMember();
    assert.equal(m.agentId, "agent-1");
    assert.equal(m.role, AgentRole.MEMBER);
    assert.deepEqual([...m.capabilities], ["tool_use"]);
  });

  it("frozen rejects mutation in strict mode", () => {
    const m = makeMember();
    // Object.freeze() in non-strict mode silently no-ops; in strict
    // mode it throws. ESM modules are strict by default.
    assert.throws(() => {
      (m as unknown as { agentId: string }).agentId = "different";
    });
  });

  it("round-trip via toDict/fromDict", () => {
    const m = makeMember("x", AgentRole.SUPERVISOR, "root");
    const rebuilt = AgentMember.fromDict(m.toDict());
    assert.equal(rebuilt.agentId, m.agentId);
    assert.equal(rebuilt.role, m.role);
    assert.equal(rebuilt.parentId, m.parentId);
    assert.deepEqual([...rebuilt.capabilities], [...m.capabilities]);
  });
});

describe("AgentTopology", () => {
  it("hub-spoke construction", () => {
    const t = makeHubSpoke();
    assert.equal(t.kind, TopologyKind.HUB_SPOKE);
    assert.equal(t.members.length, 3);
    assert.ok(t.commChannels.has("peer_messages"));
  });

  it("leader lookup", () => {
    const t = makeHubSpoke();
    const leader = t.leader();
    assert.ok(leader);
    assert.equal(leader.agentId, "lead-1");
  });

  it("leader is null for peer-to-peer", () => {
    const t = new AgentTopology({
      kind: TopologyKind.PEER_TO_PEER,
      members: [makeMember("p-1", AgentRole.PEER), makeMember("p-2", AgentRole.PEER)],
    });
    assert.equal(t.leader(), null);
  });

  it("childrenOf", () => {
    const t = makeHubSpoke();
    const children = t.childrenOf("lead-1");
    const ids = new Set(children.map((c) => c.agentId));
    assert.deepEqual(ids, new Set(["m-1", "m-2"]));
    assert.deepEqual(t.childrenOf("nonexistent"), []);
  });

  it("member lookup", () => {
    const t = makeHubSpoke();
    assert.ok(t.member("m-1"));
    assert.equal(t.member("nonexistent"), null);
  });

  it("contentHash deterministic in-process", () => {
    const t1 = makeHubSpoke();
    const t2 = makeHubSpoke();
    // topologyId differs per construction but contentHash is structural.
    assert.equal(t1.contentHash(), t2.contentHash());
  });

  it("contentHash differs for different structure", () => {
    const t1 = makeHubSpoke();
    const t2 = t1.withMember(
      makeMember("m-3", AgentRole.MEMBER, "lead-1"),
    );
    assert.notEqual(t1.contentHash(), t2.contentHash());
  });

  it("contentHash matches Python implementation", () => {
    // Cross-runtime check: spawn a Python process that builds the same
    // structural topology and prints its content_hash; compare to TS.
    const script = `
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
`;
    const result = spawnSync("python", ["-c", script], { encoding: "utf-8" });
    if (result.status !== 0) {
      // Python not installed in CI for this test environment; skip.
      return;
    }
    const pythonHash = result.stdout.trim();

    const leader = new AgentMember({
      agentId: "lead-1",
      name: "Agent lead-1",
      role: AgentRole.LEADER,
      capabilities: ["tool_use"],
    });
    const m1 = new AgentMember({
      agentId: "m-1",
      name: "Agent m-1",
      role: AgentRole.MEMBER,
      parentId: "lead-1",
      capabilities: ["tool_use"],
    });
    const ts = new AgentTopology({
      kind: TopologyKind.HUB_SPOKE,
      members: [leader, m1],
      commChannels: new Set(["peer_messages"]),
    });
    assert.equal(ts.contentHash(), pythonHash);
  });

  it("frozen rejects mutation", () => {
    const t = makeHubSpoke();
    assert.throws(() => {
      (t as unknown as { kind: string }).kind = "x";
    });
  });

  it("withMember returns new snapshot", () => {
    const t = makeHubSpoke();
    const originalHash = t.contentHash();
    const newMember = makeMember("m-3", AgentRole.MEMBER, "lead-1");
    const t2 = t.withMember(newMember);
    // Original unchanged
    assert.equal(t.contentHash(), originalHash);
    assert.equal(t.members.length, 3);
    // New snapshot has the addition
    assert.equal(t2.members.length, 4);
    assert.ok(t2.member("m-3"));
    // topologyId preserved (same logical topology)
    assert.equal(t.topologyId, t2.topologyId);
  });

  it("withMember replaces same id", () => {
    const t = makeHubSpoke();
    const updated = makeMember("m-1", AgentRole.SUPERVISOR, "lead-1");
    const t2 = t.withMember(updated);
    assert.equal(t2.members.length, 3);
    assert.equal(t2.member("m-1")?.role, AgentRole.SUPERVISOR);
  });

  it("withoutMember returns new snapshot", () => {
    const t = makeHubSpoke();
    const t2 = t.withoutMember("m-2");
    assert.equal(t.members.length, 3);
    assert.equal(t2.members.length, 2);
    assert.equal(t2.member("m-2"), null);
  });

  it("withChannels adds channels", () => {
    const t = makeHubSpoke();
    const t2 = t.withChannels("shared_memory", "broadcast_channels");
    assert.ok(t2.commChannels.has("peer_messages"));
    assert.ok(t2.commChannels.has("shared_memory"));
    assert.ok(t2.commChannels.has("broadcast_channels"));
    assert.ok(!t.commChannels.has("shared_memory"));
  });

  it("round-trip via toDict/fromDict", () => {
    const t = makeHubSpoke();
    const rebuilt = AgentTopology.fromDict(t.toDict());
    assert.equal(rebuilt.kind, t.kind);
    assert.equal(rebuilt.topologyId, t.topologyId);
    assert.equal(rebuilt.members.length, t.members.length);
    assert.equal(rebuilt.contentHash(), t.contentHash());
  });
});

describe("TopologyChange", () => {
  it("diff addition", () => {
    const t1 = makeHubSpoke();
    const newMember = makeMember("m-3", AgentRole.MEMBER, "lead-1");
    const t2 = t1.withMember(newMember);
    const change = TopologyChange.between(t1, t2);
    assert.equal(change.previousTopologyId, t1.topologyId);
    assert.equal(change.previousContentHash, t1.contentHash());
    assert.equal(change.newContentHash, t2.contentHash());
    assert.equal(change.addedMembers.length, 1);
    assert.equal(change.addedMembers[0]!.agentId, "m-3");
    assert.equal(change.removedMemberIds.length, 0);
  });

  it("diff removal", () => {
    const t1 = makeHubSpoke();
    const t2 = t1.withoutMember("m-2");
    const change = TopologyChange.between(t1, t2);
    assert.equal(change.addedMembers.length, 0);
    assert.deepEqual([...change.removedMemberIds], ["m-2"]);
  });
});
