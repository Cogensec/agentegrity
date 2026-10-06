/**
 * Topology declaration for the OpenAI Agents adapter (v0.8 Phase 11), driven by a
 * real Runner: the starting agent seeds PEER_TO_PEER and handoffs grow it.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { Agent, Runner } from "@openai/agents";
import { AgentRole, TopologyKind } from "@agentegrity/client";
import { adapter, flush, instrument, reset } from "./index.js";
import { call, say, scripted, events } from "./test-support.js";

beforeEach(() => reset());

test("the starting agent seeds a PEER_TO_PEER topology", async () => {
  const agent = new Agent({ name: "alpha", model: scripted([[say("hi")]], { input: 1, output: 1 }) });
  await instrument(new Runner()).run(agent, "hello");
  await flush();
  const t = adapter().topology!;
  assert.equal(t.kind, TopologyKind.PEER_TO_PEER);
  assert.deepEqual(t.members.map((m) => [m.agentId, m.role]), [["alpha", AgentRole.PEER]]);
});

test("handoffs grow the topology and emit subagent_start", async () => {
  const seen = events();
  const beta = new Agent({ name: "beta", model: scripted([[say("from beta")]], { input: 1, output: 1 }) });
  const alpha = new Agent({
    name: "alpha",
    model: scripted([[call("transfer_to_beta")]], { input: 1, output: 1 }),
    handoffs: [beta],
  });
  const result = await instrument(new Runner()).run(alpha, "hello");
  await flush();
  assert.equal(result.finalOutput, "from beta");
  assert.deepEqual(new Set(adapter().topology!.members.map((m) => m.agentId)), new Set(["alpha", "beta"]));
  const handoff = seen.find((e) => e.event_type === "subagent_start")!;
  assert.deepEqual(handoff.data, { agent_id: "beta", handoff_from: "alpha" });
});

test("a handoff to a known agent leaves the topology unchanged", async () => {
  const alpha: Agent = new Agent({ name: "alpha", model: scripted([[say("hi")]], { input: 1, output: 1 }) });
  await instrument(new Runner()).run(alpha, "hello");
  await flush();
  const before = adapter().topology!.contentHash();
  const loop = new Agent({
    name: "alpha",
    model: scripted([[call("transfer_to_alpha")], [say("again")]], { input: 1, output: 1 }),
  });
  loop.handoffs = [loop];
  await instrument(new Runner()).run(loop, "hello");
  await flush();
  assert.equal(adapter().topology!.contentHash(), before);
});
