/**
 * Topology declaration test for the OpenAI Agents adapter (v0.8 Phase 11).
 * Verifies seeding on onAgentStart and incremental growth on onHandoff.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { AgentRole, TopologyKind } from "@agentegrity/client";
import { adapter, reset, runHooks } from "./index.js";

interface AgentInfo {
  name: string;
}

describe("OpenAI Agents — onAgentStart seeds PEER_TO_PEER", () => {
  it("seeds with the initial agent as single PEER", async () => {
    reset();
    const hooks = runHooks() as Record<
      string,
      (...args: unknown[]) => Promise<void> | void
    >;
    const onAgentStart = hooks.onAgentStart as (
      ctx: unknown,
      agent: AgentInfo,
    ) => Promise<void>;
    await onAgentStart({}, { name: "alpha" });
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.PEER_TO_PEER);
    assert.equal(t.members.length, 1);
    assert.equal(t.members[0]!.agentId, "alpha");
    assert.equal(t.members[0]!.role, AgentRole.PEER);
  });
});

describe("OpenAI Agents — onHandoff grows topology", () => {
  it("appends handoff target as PEER", async () => {
    reset();
    const hooks = runHooks() as Record<
      string,
      (...args: unknown[]) => Promise<void> | void
    >;
    const onAgentStart = hooks.onAgentStart as (
      ctx: unknown,
      agent: AgentInfo,
    ) => Promise<void>;
    const onHandoff = hooks.onHandoff as (
      ctx: unknown,
      from: AgentInfo,
      to: AgentInfo,
    ) => Promise<void>;
    await onAgentStart({}, { name: "alpha" });
    await onHandoff({}, { name: "alpha" }, { name: "beta" });
    await onHandoff({}, { name: "beta" }, { name: "gamma" });

    const t = adapter().topology;
    assert.ok(t);
    const ids = new Set(t.members.map((m) => m.agentId));
    assert.deepEqual(ids, new Set(["alpha", "beta", "gamma"]));
    for (const m of t.members) {
      assert.equal(m.role, AgentRole.PEER);
    }
  });

  it("handoff to known agent is a no-op", async () => {
    reset();
    const hooks = runHooks() as Record<
      string,
      (...args: unknown[]) => Promise<void> | void
    >;
    const onAgentStart = hooks.onAgentStart as (
      ctx: unknown,
      agent: AgentInfo,
    ) => Promise<void>;
    const onHandoff = hooks.onHandoff as (
      ctx: unknown,
      from: AgentInfo,
      to: AgentInfo,
    ) => Promise<void>;
    await onAgentStart({}, { name: "alpha" });
    await onHandoff({}, { name: "alpha" }, { name: "alpha" });
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.members.length, 1);
  });
});
