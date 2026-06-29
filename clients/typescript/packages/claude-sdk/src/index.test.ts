import { test } from "node:test";
import assert from "node:assert/strict";
import { hooks, report, reset, registerExporter, adapter } from "./index.js";
import type { SessionExporter } from "@agentegrity/client";

function stubFetch(): { calls: Array<{ path: string; body: unknown }>; impl: typeof fetch } {
  const calls: Array<{ path: string; body: unknown }> = [];
  const impl: typeof fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      path: String(input),
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
  return { calls, impl };
}

test("hooks() returns a map of Claude SDK hook names", () => {
  reset();
  const h = hooks();
  assert.ok(typeof h.user_prompt_submit === "function");
  assert.ok(typeof h.pre_tool_use === "function");
  assert.ok(typeof h.post_tool_use === "function");
  assert.ok(typeof h.stop === "function");
});

test("hooks forward events to a registered SessionExporter (fail-open)", async () => {
  reset();
  const seen: string[] = [];
  const exporter: SessionExporter = {
    on_session_start: () => {
      seen.push("start");
    },
    on_event: (_sid, ev) => {
      seen.push(ev.event_type);
    },
    on_session_end: () => {
      seen.push("end");
    },
  };
  registerExporter(exporter);
  const h = hooks() as Record<string, (arg: unknown) => Promise<void>>;
  await h.user_prompt_submit!("hello");
  await h.pre_tool_use!({ tool_name: "Read", tool_input: { path: "/tmp/x" } });
  await h.post_tool_use!({ tool_name: "Read", tool_response: "ok" });
  await h.stop!("done");
  // session_start fires automatically before the first emit
  assert.deepEqual(seen.slice(0, 5), [
    "start",
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "stop",
  ]);
});

test("exporter exceptions never propagate to hooks", async () => {
  reset();
  const bad: SessionExporter = {
    on_event: () => {
      throw new Error("boom");
    },
  };
  registerExporter(bad);
  const h = hooks() as Record<string, (arg: unknown) => Promise<void>>;
  await h.user_prompt_submit!("still fine");
  assert.ok(true, "no throw");
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
  const a = adapter();
  const b = adapter();
  assert.equal(a, b);
});
