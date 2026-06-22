/**
 * Topology declaration test for the LangChain adapter (v0.8 Phase 11).
 * Mirrors the Python `tests/test_adapter_topology_uplift_4bc.py`
 * LangGraph cases.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { AgentRole, TopologyKind } from "@agentegrity/client";
import { adapter, instrumentGraph, reset } from "./index.js";

function makeFakeGraph(nodeKeys: string[]): {
  getGraph: () => { nodes: Record<string, unknown> };
} {
  return {
    getGraph: () => ({
      nodes: Object.fromEntries(nodeKeys.map((k) => [k, {}])),
    }),
  };
}

describe("LangChain adapter — instrumentGraph topology declaration", () => {
  it("supervisor pattern → HIERARCHICAL_DAG", () => {
    reset();
    const graph = makeFakeGraph(["supervisor", "researcher", "writer"]);
    instrumentGraph(graph);
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.HIERARCHICAL_DAG);
    assert.equal(t.members.length, 3);
    const supervisor = t.leader();
    assert.ok(supervisor);
    assert.equal(supervisor.agentId, "supervisor");
    assert.equal(supervisor.role, AgentRole.SUPERVISOR);
  });

  it("swarm pattern (no supervisor) → PEER_TO_PEER", () => {
    reset();
    const graph = makeFakeGraph(["researcher", "analyst", "writer"]);
    instrumentGraph(graph);
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.PEER_TO_PEER);
    assert.equal(t.members.length, 3);
    for (const m of t.members) {
      assert.equal(m.role, AgentRole.PEER);
    }
  });

  it("skips __start__ / __end__ sentinels", () => {
    reset();
    const graph = makeFakeGraph(["__start__", "supervisor", "worker", "__end__"]);
    instrumentGraph(graph);
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.members.length, 2);
  });

  it("graph without getGraph method → no topology", () => {
    reset();
    instrumentGraph({});
    assert.equal(adapter().topology, null);
  });
});
