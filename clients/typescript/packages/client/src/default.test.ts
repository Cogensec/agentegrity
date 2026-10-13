import { test } from "node:test";
import assert from "node:assert/strict";
import { createDefaultAdapter } from "./default.js";
import type { SessionExporter } from "./types.js";

test("createDefaultAdapter emits through registered exporters", async () => {
  const ad = createDefaultAdapter({ adapterName: "test-adapter" });
  const seen: string[] = [];
  const exp: SessionExporter = {
    on_session_start: () => { seen.push("start"); },
    on_event: (_sid, ev) => { seen.push(ev.event_type); },
    on_session_end: () => { seen.push("end"); },
  };
  ad.registerExporter(exp);

  await ad.emit({ event_type: "user_prompt_submit", data: { q: "hi" } });
  await ad.emit({ event_type: "stop", data: {} });
  await ad.end();

  assert.deepEqual(seen, ["start", "user_prompt_submit", "stop", "end"]);
});

test("AGENTEGRITY_DISABLED=1 short-circuits exporter fan-out", async () => {
  const prev = process.env.AGENTEGRITY_DISABLED;
  process.env.AGENTEGRITY_DISABLED = "1";
  try {
    const ad = createDefaultAdapter({ adapterName: "disabled" });
    const seen: string[] = [];
    ad.registerExporter({ on_event: (_s, e) => seen.push(e.event_type) });
    await ad.emit({ event_type: "user_prompt_submit", data: {} });
    assert.equal(ad.disabled, true);
    assert.deepEqual(seen, []);
  } finally {
    if (prev === undefined) delete process.env.AGENTEGRITY_DISABLED;
    else process.env.AGENTEGRITY_DISABLED = prev;
  }
});

test("exporter exceptions are swallowed (fail-open)", async () => {
  const ad = createDefaultAdapter({ adapterName: "fail-open" });
  ad.registerExporter({ on_event: () => { throw new Error("boom"); } });
  await ad.emit({ event_type: "user_prompt_submit", data: {} });
  assert.ok(true); // didn't throw
});

test("without AGENTEGRITY_URL the adapter sends nothing but still feeds exporters", async () => {
  const prevOffline = process.env.AGENTEGRITY_OFFLINE;
  const prevUrl = process.env.AGENTEGRITY_URL;
  delete process.env.AGENTEGRITY_OFFLINE;
  delete process.env.AGENTEGRITY_URL;
  let calls = 0;
  const fetchImpl = (async () => {
    calls += 1;
    return { ok: true, status: 200 } as Response;
  }) as unknown as typeof fetch;
  try {
    const ad = createDefaultAdapter({ adapterName: "local", reporterOptions: { fetchImpl } });
    const seen: string[] = [];
    ad.registerExporter({ on_event: (_s, e) => { seen.push(e.event_type); } });
    await ad.emit({ event_type: "user_prompt_submit", data: {} });
    await ad.end();
    assert.equal(calls, 0);
    assert.deepEqual(seen, ["user_prompt_submit"]);
  } finally {
    if (prevOffline === undefined) delete process.env.AGENTEGRITY_OFFLINE;
    else process.env.AGENTEGRITY_OFFLINE = prevOffline;
    if (prevUrl !== undefined) process.env.AGENTEGRITY_URL = prevUrl;
  }
});
