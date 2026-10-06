/**
 * Runs the real OpenAI Agents SDK with a scripted offline model, so these
 * tests fail if the adapter stops receiving the SDK's events.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { Agent, Runner } from "@openai/agents";
import { flush, instrument, registerExporter, report, reset } from "./index.js";
import { call, ping, say, scripted, events } from "./test-support.js";

beforeEach(() => reset());

test("a real run reaches the adapter in lifecycle order", async () => {
  const seen = events();
  const agent = new Agent({
    name: "alpha",
    model: scripted([[call("ping", '{"host":"a.example"}')], [say("done")]], { input: 100, output: 10 }),
    tools: [ping],
  });
  const runner = instrument(new Runner());
  await runner.run(agent, "hello");
  await flush();
  const types = seen.map((e) => e.event_type).filter((t) => !t.startsWith("topology"));
  assert.deepEqual(types, ["user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"]);
  const pre = seen.find((e) => e.event_type === "pre_tool_use")!;
  assert.deepEqual(pre.data, { tool_name: "ping", tool_input: { host: "a.example" } });
  const post = seen.find((e) => e.event_type === "post_tool_use")!;
  assert.equal(post.data.tool_response, "pong a.example");
});

test("usage is counted per request, with cache and reasoning", async () => {
  const seen = events();
  const agent = new Agent({
    name: "alpha",
    model: scripted([[call("ping", '{"host":"x"}')], [say("done")]], {
      input: 1000, output: 50, cached: 600, reasoning: 20,
    }),
    tools: [ping],
  });
  await instrument(new Runner({ model: "gpt-5.5" })).run(agent, "hello");
  await flush();
  const stop = seen.find((e) => e.event_type === "stop")!;
  const usage = stop.data.usage as Record<string, unknown>;
  assert.equal(usage.input_tokens, 2000);
  assert.equal(usage.cache_read_tokens, 1200);
  assert.equal(usage.reasoning_tokens, 40);
  assert.equal(usage.requests, 2);
  assert.deepEqual(Object.keys(usage.by_model as object), ["gpt-5.5"]);
  assert.equal(((await report()).usage as { total_tokens: number }).total_tokens, 2100);
});

test("instrumenting the same runner twice delivers each event once", async () => {
  const seen = events();
  const runner = new Runner();
  instrument(runner);
  instrument(runner);
  const agent = new Agent({ name: "alpha", model: scripted([[say("hi")]], { input: 1, output: 1 }) });
  await runner.run(agent, "hello");
  await flush();
  assert.equal(seen.filter((e) => e.event_type === "stop").length, 1);
});

test("exporter errors are swallowed", async () => {
  registerExporter({
    on_event: () => {
      throw new Error("boom");
    },
  });
  const agent = new Agent({ name: "alpha", model: scripted([[say("hi")]], { input: 1, output: 1 }) });
  const result = await instrument(new Runner()).run(agent, "hello");
  await flush();
  assert.equal(result.finalOutput, "hi");
});

test("report() before any run returns an empty summary", async () => {
  const summary = await report();
  assert.equal(summary.adapter, "openai_agents");
  assert.equal(summary.events, 0);
  assert.equal("usage" in summary, false);
});
