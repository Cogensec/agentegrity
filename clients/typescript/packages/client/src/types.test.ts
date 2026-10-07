/**
 * The schema lists the event types the SDK sends in `examples`; this
 * keeps the TypeScript list equal to it.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { KNOWN_EVENT_TYPES, type EventType } from "./types.js";

const common = JSON.parse(
  readFileSync(fileURLToPath(new URL("../../../../../schemas/exporter/common.json", import.meta.url)), "utf8"),
) as { $defs: { FrameworkEvent: { properties: { event_type: { examples: string[]; pattern: string } } } } };
const schema = common.$defs.FrameworkEvent.properties.event_type;

test("the known event types are the ones the schema lists", () => {
  const overflow = "<channel>_overflow";
  assert.deepEqual(
    [...KNOWN_EVENT_TYPES].sort(),
    schema.examples.filter((t) => t !== overflow).sort(),
  );
  assert.ok(schema.examples.includes(overflow));
});

test("every known type and an overflow type match the schema pattern", () => {
  const pattern = new RegExp(schema.pattern);
  const overflow: EventType = "tool_outputs_overflow";
  for (const t of [...KNOWN_EVENT_TYPES, overflow]) assert.match(t, pattern);
});
