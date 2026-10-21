/** 
 * Security-isolated AI SDK v5 compatibility tests.
 *
 * This fixture is deliberately outside the published TypeScript workspace.
 * It verifies the legacy adapter surface without requiring vulnerable
 * ai@5 / undici@5 dependencies in every modern framework build.
 * It is NOT evidence that an AI SDK v5 consumer has no known vulnerabilities.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { generateText as generateText5, streamText as streamText5 } from "ai5";
import { MockLanguageModelV2 } from "ai5/test";
import { reset, instrument, report, flush, adapter } from "../../packages/vercel-ai/src/index.ts";

beforeEach(() => reset());

const requests = () => ((adapter().getSummary().usage as { requests?: number } | undefined)?.requests ?? 0);

async function until(check: () => boolean, ms = 2000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("timed out waiting for adapter events");
    await new Promise((resolve) => setTimeout(resolve, 10));
    await flush();
  }
}

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

