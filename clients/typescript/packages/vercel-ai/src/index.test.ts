/**
 * Runs the real AI SDK (v5 and v6 through the tracer, v7 through
 * `registerTelemetry`) with its own mock models, so these tests fail if the
 * adapter stops receiving the SDK's spans or callbacks.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { generateText as generateText6, streamText as streamText6, stepCountIs, tool } from "ai";
import { MockLanguageModelV3 } from "ai/test";
import { generateText as generateText5, streamText as streamText5 } from "ai5";
import { MockLanguageModelV2 } from "ai5/test";
import { z } from "zod";
import type { FrameworkEvent } from "@agentegrity/client";
import { adapter, flush, instrument, registerExporter, report, reset, telemetry } from "./index.js";

const NODE_MAJOR = Number(process.versions.node.split(".")[0]);

/** The SDK ends streaming spans after the stream's promises resolve. */
async function until(check: () => boolean, ms = 2000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("timed out waiting for adapter events");
    await new Promise((resolve) => setTimeout(resolve, 10));
    await flush();
  }
}

const requests = () => ((adapter().getSummary().usage as { requests?: number } | undefined)?.requests ?? 0);

function events(): FrameworkEvent[] {
  const seen: FrameworkEvent[] = [];
  registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
  return seen;
}

const ping = tool({
  description: "ping",
  inputSchema: z.object({ host: z.string() }),
  execute: async ({ host }: { host: string }) => `pong ${host}`,
});

const failing = tool({
  description: "fails",
  inputSchema: z.object({}),
  execute: async () => {
    throw new Error("no route");
  },
});

/** v3 provider spec usage (ai 6 and 7): totals include the cache. */
const usageV3 = {
  inputTokens: { total: 1000, noCache: 400, cacheRead: 600, cacheWrite: 0 },
  outputTokens: { total: 50, text: 30, reasoning: 20 },
};

function scriptedV3(toolName = "ping", input = '{"host":"a"}') {
  let i = 0;
  return new MockLanguageModelV3({
    modelId: "mock-model",
    doGenerate: async () =>
      (i++ === 0
        ? {
            content: [{ type: "tool-call", toolCallId: "c1", toolName, input }],
            finishReason: { unified: "tool-calls", raw: undefined },
            usage: usageV3,
            warnings: [],
          }
        : {
            content: [{ type: "text", text: "done" }],
            finishReason: { unified: "stop", raw: undefined },
            usage: usageV3,
            warnings: [],
          }) as never,
  });
}

beforeEach(() => reset());

test("v6: a tool-using run reaches the adapter in order, with usage per model call", async () => {
  const seen = events();
  await generateText6({
    model: scriptedV3(),
    prompt: "hi",
    tools: { ping },
    stopWhen: stepCountIs(3),
    experimental_telemetry: instrument(),
  });
  await flush();
  assert.deepEqual(
    seen.map((e) => e.event_type),
    ["user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"],
  );
  assert.deepEqual(seen[1]!.data, { tool_name: "ping", tool_input: { host: "a" } });
  assert.deepEqual(seen[2]!.data, { tool_name: "ping", tool_response: "pong a" });
  const usage = seen[3]!.data.usage as Record<string, any>;
  assert.deepEqual(
    [usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens, usage.output_tokens, usage.reasoning_tokens, usage.requests],
    [2000, 1200, 0, 100, 40, 2],
  );
  assert.deepEqual(Object.keys(usage.by_model), ["mock-model"]);
});

test("v6: a failing tool reports post_tool_use_failure", async () => {
  const seen = events();
  await generateText6({
    model: scriptedV3("failing", "{}"),
    prompt: "hi",
    tools: { failing },
    stopWhen: stepCountIs(3),
    experimental_telemetry: instrument(),
  });
  await flush();
  const failure = seen.find((e) => e.event_type === "post_tool_use_failure")!;
  assert.equal(failure.data.tool_name, "failing");
  assert.match(String(failure.data.error), /no route/);
  assert.equal(seen.filter((e) => e.event_type === "post_tool_use").length, 0);
});

test("v6: streamed calls are counted once", async () => {
  const seen = events();
  const model = new MockLanguageModelV3({
    modelId: "mock-stream",
    doStream: async () =>
      ({
        stream: new ReadableStream({
          start(controller) {
            controller.enqueue({ type: "text-start", id: "t" });
            controller.enqueue({ type: "text-delta", id: "t", delta: "hello" });
            controller.enqueue({ type: "text-end", id: "t" });
            controller.enqueue({ type: "finish", finishReason: { unified: "stop", raw: undefined }, usage: usageV3 });
            controller.close();
          },
        }),
      }) as never,
  });
  const result = streamText6({ model, prompt: "hi", experimental_telemetry: instrument() });
  assert.equal(await result.text, "hello");
  await until(() => seen.some((e) => e.event_type === "stop"));
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual([usage.input_tokens, usage.requests], [1000, 1]);
  assert.equal(seen.at(-1)!.event_type, "stop");
});

test("v5: promptTokens on doGenerate and inputTokens on doStream are both read", async () => {
  const usageV2 = { inputTokens: 300, outputTokens: 20, totalTokens: 320, reasoningTokens: 5, cachedInputTokens: 100 };
  await generateText5({
    model: new MockLanguageModelV2({
      modelId: "v5-model",
      doGenerate: async () =>
        ({ content: [{ type: "text", text: "a" }], finishReason: "stop", usage: usageV2, warnings: [] }) as never,
    }),
    prompt: "hi",
    experimental_telemetry: instrument(),
  });
  const streamed = streamText5({
    model: new MockLanguageModelV2({
      modelId: "v5-model",
      doStream: async () =>
        ({
          stream: new ReadableStream({
            start(controller) {
              controller.enqueue({ type: "text-start", id: "t" });
              controller.enqueue({ type: "text-delta", id: "t", delta: "b" });
              controller.enqueue({ type: "text-end", id: "t" });
              controller.enqueue({ type: "finish", finishReason: "stop", usage: usageV2 });
              controller.close();
            },
          }),
        }) as never,
    }),
    prompt: "hi",
    experimental_telemetry: instrument(),
  });
  await streamed.text;
  await until(() => requests() >= 2);
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual([usage.input_tokens, usage.output_tokens, usage.requests], [600, 40, 2]);
});

test("v5: Anthropic input is corrected to include the cache", async () => {
  const usage = { inputTokens: 10, outputTokens: 2, totalTokens: 12, cachedInputTokens: 500 };
  const providerMetadata = { anthropic: { cacheCreationInputTokens: 50 } };
  const streamed = streamText5({
    model: new MockLanguageModelV2({
      provider: "anthropic.messages",
      modelId: "claude-x",
      doStream: async () =>
        ({
          stream: new ReadableStream({
            start(controller) {
              controller.enqueue({ type: "text-start", id: "t" });
              controller.enqueue({ type: "text-delta", id: "t", delta: "b" });
              controller.enqueue({ type: "text-end", id: "t" });
              controller.enqueue({ type: "finish", finishReason: "stop", usage, providerMetadata });
              controller.close();
            },
          }),
        }) as never,
    }),
    prompt: "hi",
    experimental_telemetry: instrument(),
  });
  await streamed.text;
  await until(() => requests() >= 1);
  const totals = (await report()).usage as Record<string, any>;
  assert.deepEqual([totals.input_tokens, totals.cache_read_tokens, totals.cache_write_tokens], [560, 500, 50]);
  assert.equal(totals.complete, true);
});

test("v5: Anthropic generateText spans omit cache reads, so the count is marked incomplete", async () => {
  await generateText5({
    model: new MockLanguageModelV2({
      provider: "anthropic.messages",
      modelId: "claude-x",
      doGenerate: async () =>
        ({
          content: [{ type: "text", text: "a" }],
          finishReason: "stop",
          usage: { inputTokens: 10, outputTokens: 2, totalTokens: 12, cachedInputTokens: 500 },
          providerMetadata: { anthropic: { cacheCreationInputTokens: 50 } },
          warnings: [],
        }) as never,
    }),
    prompt: "hi",
    experimental_telemetry: instrument(),
  });
  await flush();
  const totals = (await report()).usage as Record<string, any>;
  assert.deepEqual([totals.input_tokens, totals.cache_write_tokens, totals.complete], [60, 50, false]);
});

test("unknown spans are ignored unless catchAll", async () => {
  const seen = events();
  const { tracer } = instrument();
  tracer.startSpan("custom.thing").end();
  const { tracer: all } = instrument({ catchAll: true });
  all.startSpan("custom.other").end();
  await flush();
  assert.deepEqual(seen.map((e) => e.event_type), ["user_prompt_submit", "stop"]);
});

test("exporter errors are swallowed", async () => {
  registerExporter({
    on_event: () => {
      throw new Error("boom");
    },
  });
  const result = await generateText6({ model: scriptedV3(), prompt: "hi", tools: { ping }, experimental_telemetry: instrument() });
  await flush();
  assert.ok(result);
});

test("report() is empty before instrument", async () => {
  const s = await report();
  assert.equal(s.adapter, "vercel_ai");
  assert.equal(s.events, 0);
});

test("v7: the registered telemetry integration reports events and usage", { skip: NODE_MAJOR < 22 && "ai@7 needs Node 22" }, async () => {
  const { generateText, registerTelemetry, stepCountIs: steps, tool: tool7 } = await import("ai7");
  const { MockLanguageModelV4 } = await import("ai7/test");
  const seen = events();
  registerTelemetry(telemetry());
  let i = 0;
  const model = new MockLanguageModelV4({
    modelId: "v7-model",
    doGenerate: async () =>
      (i++ === 0
        ? {
            content: [{ type: "tool-call", toolCallId: "c1", toolName: "ping", input: '{"host":"a"}' }],
            finishReason: { unified: "tool-calls", raw: undefined },
            usage: usageV3,
            warnings: [],
          }
        : {
            content: [{ type: "text", text: "done" }],
            finishReason: { unified: "stop", raw: undefined },
            usage: usageV3,
            warnings: [],
          }) as never,
  });
  const ping7 = tool7({
    description: "ping",
    inputSchema: z.object({ host: z.string() }),
    execute: async ({ host }: { host: string }) => `pong ${host}`,
  });
  await generateText({ model, prompt: "hi", tools: { ping: ping7 }, stopWhen: steps(3) });
  await flush();
  assert.deepEqual(
    seen.map((e) => e.event_type),
    ["user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"],
  );
  assert.deepEqual(seen[1]!.data, { tool_name: "ping", tool_input: { host: "a" } });
  const usage = seen[3]!.data.usage as Record<string, any>;
  assert.deepEqual([usage.input_tokens, usage.cache_read_tokens, usage.reasoning_tokens, usage.requests], [2000, 1200, 40, 2]);
  assert.deepEqual(Object.keys(usage.by_model), ["v7-model"]);
});
