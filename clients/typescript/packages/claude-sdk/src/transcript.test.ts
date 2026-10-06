/**
 * Usage read from Claude transcripts: one line per content block repeats the
 * request's usage, so lines are keyed by requestId and the last one wins.
 * Same cases as the Python reader's tests.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { UsageLedger } from "@agentegrity/client";
import { TranscriptUsage } from "./transcript.js";

function line(request: string, out: number, opts: { stop?: string | null; model?: string } = {}): string {
  return (
    JSON.stringify({
      type: "assistant",
      requestId: request,
      message: {
        id: `msg_${request}`,
        model: opts.model ?? "claude-opus-5-5",
        stop_reason: opts.stop === undefined ? "end_turn" : opts.stop,
        usage: { input_tokens: 2, cache_read_input_tokens: 1000, cache_creation_input_tokens: 50, output_tokens: out },
      },
    }) + "\n"
  );
}

function setup(): { dir: string; ledger: UsageLedger; reader: TranscriptUsage } {
  const ledger = new UsageLedger();
  return { dir: mkdtempSync(join(tmpdir(), "transcript-")), ledger, reader: new TranscriptUsage(ledger) };
}

const totals = (ledger: UsageLedger) => ledger.toDict() as Record<string, any>;

test("cache is added to input", () => {
  const { dir, ledger, reader } = setup();
  const path = join(dir, "s.jsonl");
  writeFileSync(path, line("r1", 10));
  reader.note({ transcript_path: path });
  reader.refresh();
  const usage = totals(ledger);
  assert.equal(usage.input_tokens, 1052);
  assert.deepEqual([usage.cache_read_tokens, usage.cache_write_tokens], [1000, 50]);
  assert.equal("reasoning_tokens" in usage, false);
  rmSync(dir, { recursive: true });
});

test("repeated lines count once and the last wins", () => {
  const { dir, ledger, reader } = setup();
  const path = join(dir, "s.jsonl");
  writeFileSync(path, line("r1", 8, { stop: null }) + line("r1", 182, { stop: "tool_use" }) + line("r2", 5));
  reader.note({ transcript_path: path });
  reader.refresh();
  const usage = totals(ledger);
  assert.deepEqual([usage.requests, usage.output_tokens, usage.complete], [2, 187, true]);
  rmSync(dir, { recursive: true });
});

test("a request without its final line is incomplete", () => {
  const { dir, ledger, reader } = setup();
  const path = join(dir, "agent.jsonl");
  writeFileSync(path, line("r1", 8, { stop: null }));
  reader.note({ agent_transcript_path: path });
  reader.refresh();
  assert.equal(totals(ledger).complete, false);
  rmSync(dir, { recursive: true });
});

test("only appended complete lines are read", () => {
  const { dir, ledger, reader } = setup();
  const path = join(dir, "s.jsonl");
  writeFileSync(path, line("r1", 10) + '{"type": "assist');
  reader.note({ transcript_path: path });
  reader.refresh();
  assert.equal(totals(ledger).requests, 1);
  appendFileSync(path, 'ant"}\n' + line("r2", 20));
  reader.refresh();
  assert.deepEqual([totals(ledger).requests, totals(ledger).output_tokens], [2, 30]);
  rmSync(dir, { recursive: true });
});

test("a rewritten file is reread without double counting", () => {
  const { dir, ledger, reader } = setup();
  const path = join(dir, "s.jsonl");
  writeFileSync(path, line("r1", 10) + line("r2", 10));
  reader.note({ transcript_path: path });
  reader.refresh();
  rmSync(path);
  writeFileSync(path, JSON.stringify({ type: "system", subtype: "compact_boundary" }) + "\n" + line("r3", 1));
  reader.refresh();
  assert.deepEqual([totals(ledger).requests, totals(ledger).output_tokens, totals(ledger).complete], [3, 21, true]);
  rmSync(dir, { recursive: true });
});

test("a transcript that starts after compaction is incomplete", () => {
  const { dir, ledger, reader } = setup();
  const path = join(dir, "s.jsonl");
  writeFileSync(path, JSON.stringify({ type: "system", subtype: "compact_boundary" }) + "\n" + line("r1", 10));
  reader.note({ transcript_path: path });
  reader.refresh();
  assert.equal(totals(ledger).complete, false);
  rmSync(dir, { recursive: true });
});

test("subagent files add to the session; synthetic and missing are ignored", () => {
  const { dir, ledger, reader } = setup();
  const main = join(dir, "s.jsonl");
  const child = join(dir, "agent-a.jsonl");
  writeFileSync(main, line("r1", 10) + line("s1", 0, { model: "<synthetic>" }));
  writeFileSync(child, line("c1", 4, { model: "claude-haiku-4-5" }));
  reader.note({ transcript_path: main, agent_transcript_path: child });
  reader.note({ transcript_path: join(dir, "missing.jsonl") });
  reader.refresh();
  assert.deepEqual(Object.keys(totals(ledger).by_model).sort(), ["claude-haiku-4-5", "claude-opus-5-5"]);
  assert.equal(totals(ledger).requests, 2);
  rmSync(dir, { recursive: true });
});
