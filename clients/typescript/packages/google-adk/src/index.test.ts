/**
 * Runs the real Google ADK runner with a scripted offline model, so these
 * tests fail if the adapter stops receiving ADK's callbacks.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { BaseLlm, FunctionTool, InMemoryRunner, LlmAgent, type LlmRequest, type LlmResponse } from "@google/adk";
import type { FrameworkEvent } from "@agentegrity/client";
import { instrument, registerExporter, report, reset } from "./index.js";

/** A model that replays one list of responses per call. */
class ScriptedLlm extends BaseLlm {
  private call = 0;

  constructor(private readonly calls: Array<Array<Partial<LlmResponse>>>, model = "fake-model") {
    super({ model });
  }

  async *generateContentAsync(_request: LlmRequest): AsyncGenerator<LlmResponse, void> {
    const responses = this.calls[Math.min(this.call++, this.calls.length - 1)]!;
    for (const response of responses) yield response as LlmResponse;
  }

  async connect(): Promise<never> {
    throw new Error("live mode is not used in tests");
  }
}

const fn = (name: string, args: Record<string, unknown>) => ({
  content: { role: "model", parts: [{ functionCall: { id: `${name}-1`, name, args } }] },
});
const text = (value: string) => ({ content: { role: "model", parts: [{ text: value }] } });

const ping = new FunctionTool({
  name: "ping",
  description: "ping",
  execute: async (args: unknown) => ({ pong: (args as { host: string }).host }),
});

async function run(agent: LlmAgent): Promise<void> {
  const runner = new InMemoryRunner({ agent, appName: "app" });
  const session = await runner.sessionService.createSession({ appName: "app", userId: "u" });
  for await (const _ of runner.runAsync({
    userId: "u",
    sessionId: session.id,
    newMessage: { role: "user", parts: [{ text: "hi" }] },
  })) {
    // drain
  }
}

function events(): FrameworkEvent[] {
  const seen: FrameworkEvent[] = [];
  registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
  return seen;
}

beforeEach(() => reset());

test("a real run reaches the adapter in order, with usage per model call", async () => {
  const seen = events();
  const agent = new LlmAgent({
    name: "alpha",
    model: new ScriptedLlm([
      [{ ...fn("ping", { host: "a" }), usageMetadata: { promptTokenCount: 1000, cachedContentTokenCount: 600, candidatesTokenCount: 30, thoughtsTokenCount: 20, toolUsePromptTokenCount: 5 } }],
      [{ ...text("done"), usageMetadata: { promptTokenCount: 1000, candidatesTokenCount: 30 } }],
    ]),
    tools: [ping],
  });
  instrument(agent);
  await run(agent);
  assert.deepEqual(
    seen.map((e) => e.event_type),
    ["user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"],
  );
  assert.deepEqual(seen[1]!.data, { tool_name: "ping", tool_input: { host: "a" } });
  assert.deepEqual(seen[2]!.data, { tool_name: "ping", tool_response: { pong: "a" } });
  const usage = seen[3]!.data.usage as Record<string, any>;
  assert.deepEqual(
    [usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.reasoning_tokens, usage.requests],
    [2005, 600, 80, 20, 2],
  );
  assert.deepEqual(Object.keys(usage.by_model), ["fake-model"]);
});

test("streamed partials are skipped and each model call counts once", async () => {
  const agent = new LlmAgent({
    name: "alpha",
    model: new ScriptedLlm([
      [
        { ...text("d"), partial: true, usageMetadata: { promptTokenCount: 999, candidatesTokenCount: 1 } },
        { ...text("do"), usageMetadata: { promptTokenCount: 100, candidatesTokenCount: 2 } },
        { ...text("done"), usageMetadata: { promptTokenCount: 100, candidatesTokenCount: 4 } },
      ],
    ]),
  });
  instrument(agent);
  await run(agent);
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual([usage.input_tokens, usage.output_tokens, usage.requests], [100, 4, 1]);
});

test("the user's own callbacks still run and still decide", async () => {
  const seen: string[] = [];
  const agent = new LlmAgent({
    name: "alpha",
    model: new ScriptedLlm([[{ ...text("original"), usageMetadata: { promptTokenCount: 1, candidatesTokenCount: 1 } }]]),
    beforeAgentCallback: async () => {
      seen.push("user before");
      return undefined;
    },
    afterModelCallback: async () => {
      seen.push("user after model");
      return text("replaced") as LlmResponse;
    },
  });
  instrument(agent);
  const runner = new InMemoryRunner({ agent, appName: "app" });
  const session = await runner.sessionService.createSession({ appName: "app", userId: "u" });
  let final = "";
  for await (const ev of runner.runAsync({ userId: "u", sessionId: session.id, newMessage: { role: "user", parts: [{ text: "hi" }] } })) {
    const part = ev.content?.parts?.[0]?.text;
    if (part) final = part;
  }
  assert.deepEqual(seen, ["user before", "user after model"]);
  assert.equal(final, "replaced");
  assert.equal(((await report()).usage as Record<string, number>).requests, 1);
});

test("a transfer to a sub-agent reports subagent events and its usage", async () => {
  const seen = events();
  const beta = new LlmAgent({
    name: "beta",
    description: "helper",
    model: new ScriptedLlm([[{ ...text("from beta"), usageMetadata: { promptTokenCount: 7, candidatesTokenCount: 1 } }]], "beta-model"),
  });
  const alpha = new LlmAgent({
    name: "alpha",
    model: new ScriptedLlm([[{ ...fn("transfer_to_agent", { agent_name: "beta" }), usageMetadata: { promptTokenCount: 3, candidatesTokenCount: 1 } }]]),
    subAgents: [beta],
  });
  instrument(alpha);
  await run(alpha);
  const types = seen.map((e) => e.event_type).filter((t) => !t.startsWith("topology"));
  assert.ok(types.includes("subagent_start"), types.join(","));
  assert.ok(types.includes("subagent_stop"), types.join(","));
  const start = seen.find((e) => e.event_type === "subagent_start")!;
  assert.equal(start.data.agent_id, "beta");
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual(Object.keys(usage.by_model).sort(), ["beta-model", "fake-model"]);
});

test("instrumenting the same agent twice delivers each event once", async () => {
  const seen = events();
  const agent = new LlmAgent({
    name: "alpha",
    model: new ScriptedLlm([[{ ...text("hi"), usageMetadata: { promptTokenCount: 1, candidatesTokenCount: 1 } }]]),
  });
  instrument(agent);
  instrument(agent);
  await run(agent);
  assert.equal(seen.filter((e) => e.event_type === "stop").length, 1);
});

test("exporter errors are swallowed", async () => {
  registerExporter({
    on_event: () => {
      throw new Error("boom");
    },
  });
  const agent = new LlmAgent({ name: "alpha", model: new ScriptedLlm([[text("hi")]]) });
  instrument(agent);
  await run(agent);
});

test("report() empty before instrument", async () => {
  const s = await report();
  assert.equal(s.adapter, "google_adk");
  assert.equal(s.events, 0);
});
