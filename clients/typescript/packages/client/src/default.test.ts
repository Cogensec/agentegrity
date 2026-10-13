import { test } from "node:test";
import assert from "node:assert/strict";
import { createDefaultAdapter, emptySummary } from "./default.js";
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

async function withEnv<T>(env: Record<string, string | undefined>, run: () => Promise<T>): Promise<T> {
  const saved: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(env)) {
    saved[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  try {
    return await run();
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

const okFetch = (async () => ({ ok: true, status: 200 }) as Response) as unknown as typeof fetch;

test("the summary reports no chain, since TypeScript evaluates and attests nothing locally", async () => {
  await withEnv({ AGENTEGRITY_URL: undefined, AGENTEGRITY_OFFLINE: undefined }, async () => {
    const ad = createDefaultAdapter({ adapterName: "honest", reporterOptions: { fetchImpl: okFetch } });
    await ad.emit({ event_type: "user_prompt_submit", data: {} });
    const s = ad.getSummary();
    assert.equal(s.evaluations, 0);
    assert.equal(s.attestation_records, 0);
    assert.equal(s.chain_hash_linked, null);
    assert.equal(s.chain_signatures, "none");
    assert.deepEqual(s.exporters, []);
  });
});

test("the summary names the HTTP destination by origin only", async () => {
  await withEnv({ AGENTEGRITY_URL: undefined, AGENTEGRITY_OFFLINE: undefined }, async () => {
    const ad = createDefaultAdapter({
      adapterName: "remote",
      reporterOptions: { baseUrl: "https://ingest.example.com/v1/", apiKey: "secret-token", fetchImpl: okFetch },
    });
    await ad.emit({ event_type: "user_prompt_submit", data: {} });
    const exporters = ad.getSummary().exporters;
    assert.deepEqual(exporters, [{ type: "AgentegrityReporter", target: "https://ingest.example.com" }]);
    assert.ok(!JSON.stringify(exporters).includes("secret-token"));
  });
});

test("offline mode and unsafe URLs report no HTTP export", async () => {
  await withEnv({ AGENTEGRITY_URL: undefined, AGENTEGRITY_OFFLINE: "1" }, async () => {
    const ad = createDefaultAdapter({
      adapterName: "offline",
      reporterOptions: { baseUrl: "https://ingest.example.com", fetchImpl: okFetch },
    });
    assert.deepEqual(ad.getSummary().exporters, []);
  });
  await withEnv({ AGENTEGRITY_URL: undefined, AGENTEGRITY_OFFLINE: undefined }, async () => {
    const ad = createDefaultAdapter({
      adapterName: "unsafe",
      reporterOptions: { baseUrl: "http://example.test", fetchImpl: okFetch, onError: () => {} },
    });
    assert.deepEqual(ad.getSummary().exporters, []);
  });
});

test("registered exporters appear in the summary", async () => {
  await withEnv({ AGENTEGRITY_URL: undefined }, async () => {
    const ad = createDefaultAdapter({ adapterName: "sinks", reporterOptions: { fetchImpl: okFetch } });
    ad.registerExporter({ describe: () => ({ type: "Custom", target: "https://sink.example.com" }) });
    ad.registerExporter({ on_event: () => {} });
    assert.deepEqual(ad.getSummary().exporters, [
      { type: "Custom", target: "https://sink.example.com" },
      { type: "SessionExporter" },
    ]);
  });
});

test("emptySummary reports no chain and no sinks", () => {
  const s = emptySummary("claude");
  assert.equal(s.adapter, "claude");
  assert.equal(s.chain_hash_linked, null);
  assert.equal(s.chain_signatures, "none");
  assert.deepEqual(s.exporters, []);
});
