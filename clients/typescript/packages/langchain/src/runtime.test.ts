/**
 * Runs real @langchain/core runnables, tools and a chat model that reports
 * usage, so these tests fail if LangChain stops delivering callbacks the
 * way the handler expects.
 */
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { BaseChatModel } from "@langchain/core/language_models/chat_models";
import { AIMessage, type BaseMessage, type UsageMetadata } from "@langchain/core/messages";
import type { ChatResult } from "@langchain/core/outputs";
import { RunnableLambda } from "@langchain/core/runnables";
import { tool } from "@langchain/core/tools";
import { z } from "zod";
import type { FrameworkEvent } from "@agentegrity/client";
import { instrument, registerExporter, report, reset } from "./index.js";

/** A chat model that answers "ok" and reports fixed usage. */
class UsageChat extends BaseChatModel {
  constructor(
    private readonly usage: UsageMetadata,
    private readonly modelName = "usage-model",
  ) {
    super({});
  }

  _llmType(): string {
    return "usage-chat";
  }

  async _generate(_messages: BaseMessage[]): Promise<ChatResult> {
    const message = new AIMessage({
      content: "ok",
      usage_metadata: this.usage,
      response_metadata: { model_name: this.modelName },
    });
    return { generations: [{ text: "ok", message }] };
  }
}

const usage = (input: number, output: number, details: Partial<UsageMetadata> = {}): UsageMetadata => ({
  input_tokens: input,
  output_tokens: output,
  total_tokens: input + output,
  ...details,
});

function events(): FrameworkEvent[] {
  const seen: FrameworkEvent[] = [];
  registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
  return seen;
}

beforeEach(() => reset());

test("nested chains emit one prompt and one stop per top-level run", async () => {
  const seen = events();
  const inner = RunnableLambda.from(async (x: string) => `${x}!`);
  const outer = RunnableLambda.from(async (x: string, config) => inner.invoke(x, config));
  const handler = instrument();
  await outer.invoke("a", { callbacks: [handler] });
  await outer.invoke("b", { callbacks: [handler] });
  assert.deepEqual(
    seen.map((e) => e.event_type),
    ["user_prompt_submit", "stop", "user_prompt_submit", "stop"],
  );
});

test("tool events carry the tool name at start and end, and failures", async () => {
  const seen = events();
  const ping = tool(async ({ host }: { host: string }) => `pong ${host}`, {
    name: "ping",
    description: "ping",
    schema: z.object({ host: z.string() }),
  });
  const broken = tool(
    async () => {
      throw new Error("no route");
    },
    { name: "broken", description: "fails", schema: z.object({}) },
  );
  const chain = RunnableLambda.from(async (_: string, config) => {
    await ping.invoke({ host: "a" }, config);
    await broken.invoke({}, config).catch(() => undefined);
    return "done";
  });
  await chain.invoke("go", { callbacks: [instrument()] });
  const tools = seen.filter((e) => e.event_type.includes("tool"));
  assert.deepEqual(
    tools.map((e) => [e.event_type, e.data.tool_name]),
    [
      ["pre_tool_use", "ping"],
      ["post_tool_use", "ping"],
      ["pre_tool_use", "broken"],
      ["post_tool_use_failure", "broken"],
    ],
  );
});

test("model usage is counted per call and reaches the stop event", async () => {
  const seen = events();
  const model = new UsageChat(
    usage(1100, 40, { input_token_details: { cache_read: 900, cache_creation: 100 }, output_token_details: { reasoning: 12 } }),
  );
  const chain = RunnableLambda.from(async (x: string, config) => {
    await model.invoke(x, config);
    await model.invoke(x, config);
    return "done";
  });
  await chain.invoke("hi", { callbacks: [instrument()] });
  const stop = seen.find((e) => e.event_type === "stop")!;
  const totals = stop.data.usage as Record<string, any>;
  assert.deepEqual(
    [totals.input_tokens, totals.cache_read_tokens, totals.cache_write_tokens, totals.reasoning_tokens, totals.requests],
    [2200, 1800, 200, 24, 2],
  );
  assert.deepEqual(Object.keys(totals.by_model), ["usage-model"]);
});

test("a handler attached twice counts each model call once", async () => {
  const handler = instrument();
  const model = new UsageChat(usage(10, 1)).withConfig({ callbacks: [handler] });
  await model.invoke("hi", { callbacks: [handler] });
  assert.equal(((await report()).usage as Record<string, number>).requests, 1);
});

test("tier-prefixed detail keys are counted", async () => {
  const model = new UsageChat(
    usage(500, 5, {
      input_token_details: { priority_cache_read: 300 } as UsageMetadata["input_token_details"],
      output_token_details: { priority_reasoning: 3 } as UsageMetadata["output_token_details"],
    }),
  );
  await model.invoke("hi", { callbacks: [instrument()] });
  const totals = (await report()).usage as Record<string, number>;
  assert.deepEqual([totals.cache_read_tokens, totals.reasoning_tokens], [300, 3]);
});
