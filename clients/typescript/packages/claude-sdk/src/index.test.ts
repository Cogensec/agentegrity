import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { HookCallbackMatcher, HookEvent, HookInput, Options } from "@anthropic-ai/claude-agent-sdk";
import type { FrameworkEvent, SessionExporter } from "@agentegrity/client";
import { hooks, observe, report, reset, registerExporter, adapter } from "./index.js";

const base = { session_id: "s-1", transcript_path: "/nonexistent/s.jsonl", cwd: "/tmp" };

/** Call our callback for one event the way the SDK does: matcher list, then hooks. */
async function fire(event: HookEvent, input: Record<string, unknown>): Promise<unknown> {
  const matchers = hooks()[event] as HookCallbackMatcher[];
  assert.ok(matchers?.length, `no matcher for ${event}`);
  const signal = new AbortController().signal;
  return matchers[0]!.hooks[0]!({ ...base, hook_event_name: event, ...input } as HookInput, undefined, { signal });
}

function collect(): FrameworkEvent[] {
  const seen: FrameworkEvent[] = [];
  registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
  return seen;
}

test("hooks() matches the SDK's Options['hooks'] shape", () => {
  reset();
  const options: Options = { hooks: hooks() };
  assert.deepEqual(Object.keys(options.hooks!).sort(), [
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "PreToolUse",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
  ]);
});

test("hook inputs reach a registered SessionExporter in order", async () => {
  reset();
  const seen: string[] = [];
  const exporter: SessionExporter = {
    on_session_start: () => void seen.push("start"),
    on_event: (_sid, ev) => void seen.push(ev.event_type),
  };
  registerExporter(exporter);
  await fire("UserPromptSubmit", { prompt: "hello" });
  await fire("PreToolUse", { tool_name: "Read", tool_input: { file_path: "/tmp/x" }, tool_use_id: "t1" });
  await fire("PostToolUse", { tool_name: "Read", tool_input: {}, tool_response: "ok", tool_use_id: "t1" });
  await fire("PostToolUseFailure", { tool_name: "Bash", tool_input: {}, error: "exit 1", tool_use_id: "t2" });
  await fire("Stop", { stop_hook_active: false, last_assistant_message: "done" });
  assert.deepEqual(seen, [
    "start",
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "stop",
  ]);
});

test("event data carries the hook's fields", async () => {
  reset();
  const seen = collect();
  await fire("UserPromptSubmit", { prompt: "hello" });
  await fire("PreToolUse", { tool_name: "Read", tool_input: { file_path: "/tmp/x" }, tool_use_id: "t1" });
  await fire("PostToolUseFailure", { tool_name: "Bash", tool_input: {}, error: "exit 1", tool_use_id: "t2" });
  await fire("SubagentStart", { agent_id: "a1", agent_type: "explore" });
  assert.equal(seen[0]!.data.prompt, "hello");
  assert.deepEqual(seen[1]!.data, { tool_name: "Read", tool_input: { file_path: "/tmp/x" } });
  assert.deepEqual(seen[2]!.data, { tool_name: "Bash", error: "exit 1" });
  assert.deepEqual(seen[3]!.data, { agent_id: "a1", agent_type: "explore" });
});

test("hooks return an empty output so the SDK continues", async () => {
  reset();
  assert.deepEqual(await fire("PreToolUse", { tool_name: "Read", tool_input: {}, tool_use_id: "t1" }), {});
});

test("exporter exceptions never propagate to hooks", async () => {
  reset();
  registerExporter({
    on_event: () => {
      throw new Error("boom");
    },
  });
  assert.deepEqual(await fire("UserPromptSubmit", { prompt: "still fine" }), {});
});

test("Stop reads usage from the transcripts the hooks named", async () => {
  reset();
  const seen = collect();
  const dir = mkdtempSync(join(tmpdir(), "claude-sdk-"));
  const main = join(dir, "s.jsonl");
  const child = join(dir, "agent-a1.jsonl");
  const usageLine = (request: string, model: string) =>
    JSON.stringify({
      type: "assistant",
      requestId: request,
      message: {
        model,
        stop_reason: "end_turn",
        usage: { input_tokens: 3, cache_read_input_tokens: 100, cache_creation_input_tokens: 10, output_tokens: 20 },
      },
    }) + "\n";
  writeFileSync(main, usageLine("r1", "claude-opus-5-5"));
  writeFileSync(child, usageLine("c1", "claude-haiku-4-5"));
  await fire("SubagentStop", {
    transcript_path: main,
    agent_id: "a1",
    agent_type: "explore",
    agent_transcript_path: child,
    stop_hook_active: false,
  });
  await fire("Stop", { transcript_path: main, stop_hook_active: false });
  const usage = seen.find((e) => e.event_type === "stop")!.data.usage as Record<string, any>;
  assert.equal(usage.input_tokens, 226);
  assert.deepEqual(Object.keys(usage.by_model).sort(), ["claude-haiku-4-5", "claude-opus-5-5"]);
  assert.deepEqual(usage.sources, ["transcript"]);
});

const modelUsage = (input: number, output: number) => ({
  inputTokens: input,
  outputTokens: output,
  thinkingTokens: 4,
  cacheReadInputTokens: 1000,
  cacheCreationInputTokens: 100,
  webSearchRequests: 0,
  costUSD: 0.01,
  contextWindow: 200000,
  maxOutputTokens: 32000,
});

test("observe() replaces the transcript estimate with result totals", async () => {
  reset();
  const dir = mkdtempSync(join(tmpdir(), "claude-sdk-"));
  const main = join(dir, "s.jsonl");
  writeFileSync(
    main,
    JSON.stringify({
      type: "assistant",
      requestId: "r1",
      message: { model: "m", stop_reason: "end_turn", usage: { input_tokens: 1, output_tokens: 1 } },
    }) + "\n",
  );
  await fire("Stop", { transcript_path: main, stop_hook_active: false });
  observe({ type: "result", modelUsage: { "claude-opus-5-5": modelUsage(5, 40) } });
  observe({ type: "result", modelUsage: { "claude-opus-5-5": modelUsage(9, 70) } });
  await fire("Stop", { transcript_path: main, stop_hook_active: false });
  const usage = (await report()).usage as Record<string, any>;
  assert.deepEqual(usage.sources, ["provider_response"]);
  assert.deepEqual([usage.input_tokens, usage.output_tokens, usage.reasoning_tokens], [1109, 70, 4]);
});

test("observe() keeps the totals from before a conversation reset", async () => {
  reset();
  observe({ type: "result", modelUsage: { "claude-opus-5-5": modelUsage(5, 40) } });
  observe({ type: "conversation_reset" });
  observe({ type: "result", modelUsage: { "claude-opus-5-5": modelUsage(1, 3) } });
  observe({ type: "assistant", message: {} });
  assert.equal(((await report()).usage as Record<string, any>).output_tokens, 43);
});

test("report() returns a well-formed empty summary before first hook call", async () => {
  reset();
  const s = await report();
  assert.equal(s.adapter, "claude");
  assert.equal(s.events, 0);
  assert.equal(s.chain_hash_linked, true);
});

test("adapter() returns a stable DefaultAdapter across calls", () => {
  reset();
  assert.equal(adapter(), adapter());
});
