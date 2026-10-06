/**
 * Token usage ledger. The vectors are generated from the Python
 * `UsageLedger` and asserted by both suites, so the two runtimes report
 * byte-identical usage for the same calls.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { UsageLedger, type TokenUsage } from "./usage.js";
import { createDefaultAdapter } from "./default.js";

interface Vector {
  name: string;
  records: Array<{
    key: string;
    model: string | null;
    usage: TokenUsage;
    source: string;
    complete: boolean;
  }>;
  mark_incomplete: boolean;
  expected: Record<string, unknown> | null;
}

const VECTOR_PATH = fileURLToPath(
  new URL("../../../../../tests/fixtures/usage_ledger_vectors.json", import.meta.url),
);
const vectors: Vector[] = JSON.parse(readFileSync(VECTOR_PATH, "utf8"));

test("loads the shared usage vectors", () => {
  assert.ok(vectors.length > 0);
});

for (const vector of vectors) {
  test(`matches the Python ledger: ${vector.name}`, () => {
    const ledger = new UsageLedger();
    for (const r of vector.records) {
      ledger.record(r.key, r.model, r.usage, { source: r.source, complete: r.complete });
    }
    if (vector.mark_incomplete) ledger.markIncomplete();
    assert.deepEqual(ledger.toDict(), vector.expected);
  });
}

test("discard drops one source", () => {
  const ledger = new UsageLedger();
  ledger.record("t", "m", { input_tokens: 5, output_tokens: 5 }, { source: "transcript" });
  ledger.record("p", "m", { input_tokens: 9, output_tokens: 9 }, { source: "provider_response" });
  ledger.discard("transcript");
  assert.equal(ledger.toDict()?.input_tokens, 9);
});

function offlineAdapter(modelId?: string) {
  return createDefaultAdapter({
    adapterName: "probe",
    profile: modelId ? { model_id: modelId } : undefined,
    reporterOptions: { fetchImpl: async () => new Response("{}"), onError: () => {} },
  });
}

test("stop events and the summary carry the running total", async () => {
  const adapter = offlineAdapter();
  const seen: Array<Record<string, unknown>> = [];
  adapter.registerExporter({ on_event: (_s, ev) => void seen.push(ev.data) });
  adapter.recordUsage("c1", "gpt", { input_tokens: 10, output_tokens: 2 }, { source: "provider_response" });
  await adapter.emit({ event_type: "pre_tool_use", data: { tool_name: "x" } });
  await adapter.emit({ event_type: "stop", data: { output: "done" } });
  assert.equal(seen[0]?.usage, undefined);
  assert.equal((seen[1]?.usage as { total_tokens: number }).total_tokens, 12);
  adapter.recordUsage("c2", "gpt", { input_tokens: 5, output_tokens: 1 }, { source: "provider_response" });
  assert.equal((adapter.getSummary().usage as { total_tokens: number }).total_tokens, 18);
});

test("no usage means no usage field", async () => {
  const adapter = offlineAdapter();
  assert.equal("usage" in adapter.getSummary(), false);
});

test("an unnamed model falls back to the profile model", () => {
  const adapter = offlineAdapter("configured-model");
  adapter.recordUsage("c1", null, { input_tokens: 1, output_tokens: 1 }, { source: "trace" });
  const usage = adapter.getSummary().usage as { by_model: Record<string, unknown> };
  assert.deepEqual(Object.keys(usage.by_model), ["configured-model"]);
});

test("reset starts a fresh ledger", () => {
  const adapter = offlineAdapter();
  adapter.recordUsage("c1", "m", { input_tokens: 1, output_tokens: 1 }, { source: "trace" });
  adapter.reset();
  assert.equal("usage" in adapter.getSummary(), false);
});
