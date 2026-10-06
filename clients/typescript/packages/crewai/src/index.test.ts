/**
 * Drives the adapter through a bus with CrewAI's `on(type, handler)` shape,
 * so the mapping is covered on Node versions CrewAI itself does not run on.
 * runtime.test.ts checks the same behavior against the real framework.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import type { FrameworkEvent } from "@agentegrity/client";
import { instrument, registerExporter, report, reset, type CrewAIEventBus } from "./index.js";

type Handler = (source: unknown, event: Record<string, unknown>) => void;

class Bus implements CrewAIEventBus {
  readonly handlers = new Map<string, Set<Handler>>();

  on(type: string, handler: Handler): () => void {
    const set = this.handlers.get(type) ?? new Set();
    this.handlers.set(type, set);
    set.add(handler);
    return () => set.delete(handler);
  }

  emit(type: string, source: unknown, event: Record<string, unknown> = {}): void {
    for (const handler of this.handlers.get(type) ?? []) handler(source, event);
  }

  get size(): number {
    return [...this.handlers.values()].reduce((n, set) => n + set.size, 0);
  }
}

function events(): FrameworkEvent[] {
  const seen: FrameworkEvent[] = [];
  registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
  return seen;
}

const cumulative = (prompt: number, completion: number, requests: number, cached = 0) => ({
  promptTokens: prompt,
  completionTokens: completion,
  cachedPromptTokens: cached,
  reasoningTokens: 0,
  cacheCreationTokens: 0,
  totalTokens: prompt + completion,
  successfulRequests: requests,
});

beforeEach(() => reset());

test("lifecycle events map to agentegrity events in order", async () => {
  const seen = events();
  const bus = new Bus();
  const close = instrument(bus);
  const agent = { role: "researcher" };
  bus.emit("crew_kickoff_started", {}, { inputs: { topic: "x" } });
  bus.emit("task_started", { id: "t1", description: "find", agent }, {});
  bus.emit("agent_execution_started", agent, { agent });
  bus.emit("tool_usage_started", {}, { tool_name: "search", tool_args: { q: "x" } });
  bus.emit("tool_usage_finished", {}, { tool_name: "search", output: "hits" });
  bus.emit("tool_usage_error", {}, { tool_name: "fetch", error: "timeout" });
  bus.emit("agent_execution_completed", agent, { agent });
  bus.emit("crew_kickoff_completed", {}, { output: { raw: "report" } });
  await close();
  assert.deepEqual(
    seen.map((e) => [e.event_type, e.data]),
    [
      ["user_prompt_submit", { prompt: { topic: "x" } }],
      ["task_started", { task_id: "t1", description: "find", agent_id: "researcher" }],
      ["subagent_start", { agent_id: "researcher" }],
      ["pre_tool_use", { tool_name: "search", tool_input: { q: "x" } }],
      ["post_tool_use", { tool_name: "search", tool_response: "hits" }],
      ["post_tool_use_failure", { tool_name: "fetch", error: "timeout" }],
      ["subagent_stop", { agent_id: "researcher" }],
      ["stop", { output: "report" }],
    ],
  );
});

test("failures close the agent and the run", async () => {
  const seen = events();
  const bus = new Bus();
  const close = instrument(bus);
  bus.emit("agent_execution_error", {}, { agent: { role: "a" }, error: "boom" });
  bus.emit("crew_kickoff_failed", {}, { error: "boom" });
  await close();
  assert.deepEqual(
    seen.map((e) => [e.event_type, e.data]),
    [
      ["subagent_stop", { agent_id: "a", error: "boom" }],
      ["stop", { error: "boom" }],
    ],
  );
});

test("per-call usage wins over the client's running total for the same call", async () => {
  const bus = new Bus();
  const close = instrument(bus);
  const llm = { model: "m" };
  bus.emit("llm_call_completed", llm, {
    call_id: "c1",
    model: "m",
    usage: { prompt_tokens: 1000, completion_tokens: 50, prompt_tokens_details: { cached_tokens: 800 } },
  });
  bus.emit("llm_call_completed", llm, { call_id: "c1", model: "m", usage: cumulative(1000, 50, 1, 800) });
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual([usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.requests], [1000, 800, 50, 1]);
  assert.deepEqual(Object.keys(usage.by_model), ["m"]);
  await close();
});

test("running totals are counted as deltas per client, and a reset starts over", async () => {
  const bus = new Bus();
  const close = instrument(bus);
  const a = { model: "a" };
  const b = { model: "b" };
  bus.emit("llm_call_completed", a, { call_id: "1", usage: cumulative(100, 10, 1) });
  bus.emit("llm_call_completed", b, { call_id: "2", usage: cumulative(7, 1, 1) });
  bus.emit("llm_call_completed", a, { call_id: "3", usage: cumulative(300, 30, 2) });
  // The client's metrics were reset, so this total is one new call.
  bus.emit("llm_call_completed", a, { call_id: "4", usage: cumulative(40, 4, 1) });
  // Nothing new since the last total: an event without a model call.
  bus.emit("llm_call_completed", a, { call_id: "5", usage: cumulative(40, 4, 1) });
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual([usage.input_tokens, usage.output_tokens, usage.requests], [347, 35, 4]);
  assert.deepEqual(usage.by_model.a.input_tokens, 340);
  assert.deepEqual(usage.by_model.b.input_tokens, 7);
  await close();
});

test("calls without usage are not counted", async () => {
  const bus = new Bus();
  const close = instrument(bus);
  bus.emit("llm_call_completed", { model: "m" }, { call_id: "c1", call_type: "tool_call", usage: null });
  assert.equal((await report()).usage, undefined);
  await close();
});

test("gemini usage adds thinking tokens to the output", async () => {
  const bus = new Bus();
  const close = instrument(bus);
  bus.emit("llm_call_completed", {}, {
    call_id: "g",
    model: "gemini",
    usage: { prompt_token_count: 300, cached_content_token_count: 100, candidates_token_count: 20, thoughts_token_count: 12 },
  });
  const usage = (await report()).usage as Record<string, number>;
  assert.deepEqual(
    [usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.reasoning_tokens],
    [300, 100, 32, 12],
  );
  await close();
});

test("instrumenting a bus twice subscribes once; close() unsubscribes", async () => {
  const bus = new Bus();
  const close = instrument(bus);
  const subscribed = bus.size;
  assert.ok(subscribed > 0);
  assert.equal(instrument(bus), close);
  assert.equal(bus.size, subscribed);
  await close();
  assert.equal(bus.size, 0);
});

test("exporter errors are swallowed", async () => {
  registerExporter({
    on_event: () => {
      throw new Error("boom");
    },
  });
  const bus = new Bus();
  const close = instrument(bus);
  bus.emit("crew_kickoff_started", {}, {});
  await close();
});

test("report() before instrument() returns empty", async () => {
  const s = await report();
  assert.equal(s.adapter, "crewai");
  assert.equal(s.events, 0);
});
