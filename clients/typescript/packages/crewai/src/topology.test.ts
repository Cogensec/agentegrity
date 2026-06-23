/**
 * Topology declaration test for the CrewAI adapter (v0.8 Phase 11).
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { AgentRole, TopologyKind } from "@agentegrity/client";
import { adapter, instrument, reset } from "./index.js";

describe("CrewAI adapter — instrument({ crew }) topology", () => {
  it("sequential crew → HUB_SPOKE", () => {
    reset();
    const crew = {
      agents: [
        { role: "researcher" },
        { role: "analyst" },
        { role: "writer" },
      ],
      process: "sequential",
    };
    instrument({ crew });
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.HUB_SPOKE);
    assert.equal(t.members.length, 3);
    const leader = t.leader();
    assert.ok(leader);
    assert.equal(leader.agentId, "researcher");
  });

  it("hierarchical crew → HIERARCHICAL_DAG", () => {
    reset();
    const crew = {
      agents: [{ role: "manager" }, { role: "worker" }],
      process: "hierarchical",
    };
    instrument({ crew });
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.HIERARCHICAL_DAG);
  });

  it("instrument() without crew → no topology", () => {
    reset();
    instrument();
    assert.equal(adapter().topology, null);
  });

  it("members get LEADER + MEMBER roles with parent linkage", () => {
    reset();
    const crew = {
      agents: [{ role: "lead" }, { role: "m1" }, { role: "m2" }],
      process: "sequential",
    };
    instrument({ crew });
    const t = adapter().topology;
    assert.ok(t);
    const lead = t.member("lead");
    const m1 = t.member("m1");
    const m2 = t.member("m2");
    assert.equal(lead?.role, AgentRole.LEADER);
    assert.equal(m1?.role, AgentRole.MEMBER);
    assert.equal(m1?.parentId, "lead");
    assert.equal(m2?.role, AgentRole.MEMBER);
    assert.equal(m2?.parentId, "lead");
  });
});
