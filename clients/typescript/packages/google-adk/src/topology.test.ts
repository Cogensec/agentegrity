/**
 * Topology declaration test for the Google ADK adapter (v0.8 Phase 11).
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { AgentRole, TopologyKind } from "@agentegrity/client";
import { adapter, instrument, reset } from "./index.js";

describe("Google ADK adapter — instrument(agent) topology", () => {
  it("agent with sub_agents → HIERARCHICAL_DAG", () => {
    reset();
    const adkAgent = {
      name: "researcher_workflow",
      sub_agents: [{ name: "fetch" }, { name: "summarize" }],
      addBeforeAgentCallback: (_fn: unknown) => {
        /* no-op */
      },
      addAfterAgentCallback: (_fn: unknown) => {
        /* no-op */
      },
      addBeforeToolCallback: (_fn: unknown) => {
        /* no-op */
      },
      addAfterToolCallback: (_fn: unknown) => {
        /* no-op */
      },
    };
    instrument(adkAgent);
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.HIERARCHICAL_DAG);
    assert.equal(t.members.length, 3);
    const supervisor = t.leader();
    assert.ok(supervisor);
    assert.equal(supervisor.role, AgentRole.SUPERVISOR);
    assert.equal(supervisor.agentId, "researcher_workflow");
  });

  it("plain agent (no sub_agents) → no topology", () => {
    reset();
    const adkAgent = {
      name: "simple",
      addBeforeAgentCallback: (_fn: unknown) => {
        /* no-op */
      },
      addAfterAgentCallback: (_fn: unknown) => {
        /* no-op */
      },
      addBeforeToolCallback: (_fn: unknown) => {
        /* no-op */
      },
      addAfterToolCallback: (_fn: unknown) => {
        /* no-op */
      },
    };
    instrument(adkAgent);
    assert.equal(adapter().topology, null);
  });

  it("camelCase subAgents also recognized", () => {
    reset();
    const adkAgent = {
      name: "wf",
      subAgents: [{ name: "child" }],
      addBeforeAgentCallback: (_fn: unknown) => {
        /* no-op */
      },
      addAfterAgentCallback: (_fn: unknown) => {
        /* no-op */
      },
      addBeforeToolCallback: (_fn: unknown) => {
        /* no-op */
      },
      addAfterToolCallback: (_fn: unknown) => {
        /* no-op */
      },
    };
    instrument(adkAgent);
    const t = adapter().topology;
    assert.ok(t);
    assert.equal(t.kind, TopologyKind.HIERARCHICAL_DAG);
    assert.equal(t.members.length, 2);
  });
});
