/** Read exactly the same positive and negative vectors as Python's tests. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { parseAgentCapabilityCard, parseCanonicalAgentEvent } from "./agent-events.js";

interface Vectors {
  valid_events: unknown[];
  invalid_events: unknown[];
  valid_cards: unknown[];
  invalid_cards: unknown[];
}

const vectors = JSON.parse(readFileSync(fileURLToPath(new URL(
  "../../../../../tests/fixtures/agent_event_contract_v1.json",
  import.meta.url,
)), "utf8")) as Vectors;

test("canonical event vectors have coverage", () => {
  assert.ok(vectors.valid_events.length >= 2);
  assert.ok(vectors.invalid_events.length >= 5);
});

for (const [index, event] of vectors.valid_events.entries()) {
  test(`accept canonical agent event fixture ${index}`, () => {
    assert.deepEqual(parseCanonicalAgentEvent(event), event);
  });
}
for (const [index, event] of vectors.invalid_events.entries()) {
  test(`reject malformed agent event fixture ${index}`, () => {
    assert.throws(() => parseCanonicalAgentEvent(event));
  });
}
for (const [index, card] of vectors.valid_cards.entries()) {
  test(`accept capability card fixture ${index}`, () => {
    assert.deepEqual(parseAgentCapabilityCard(card), card);
  });
}
for (const [index, card] of vectors.invalid_cards.entries()) {
  test(`reject capability card fixture ${index}`, () => {
    assert.throws(() => parseAgentCapabilityCard(card));
  });
}
