/**
 * Token usage read from Claude transcripts, the TypeScript port of the
 * Python `ClaudeTranscriptUsage` reader.
 *
 * Hook inputs name the session transcript (`transcript_path`) and, on
 * SubagentStop, the child's (`agent_transcript_path`). Each file is read
 * from where the last read stopped. A request is written once per content
 * block with its usage repeated, so lines are keyed by `requestId` and the
 * last one wins; early lines carry a placeholder output count and no
 * `stop_reason`. Anthropic counts cache outside `input_tokens`, so it is
 * added back. Only usage counts, request ids and model names are kept.
 */
import { closeSync, openSync, readSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import type { UsageLedger } from "@agentegrity/client";

export const TRANSCRIPT_SOURCE = "transcript";
const PATH_KEYS = ["transcript_path", "agent_transcript_path"] as const;
const CHUNK = 1 << 20;
const HEAD_BYTES = 65536;

/** Complete lines appended to a JSONL file since the last read. */
class JsonlTail {
  fresh = true;
  private offset = 0;
  private identity: string | null = null;
  private head: string | null = null;

  constructor(readonly path: string) {}

  /** New complete lines; a line still being written waits for the next read. */
  read(): string[] {
    let size: number;
    let identity: string;
    try {
      const stat = statSync(this.path);
      size = stat.size;
      identity = `${stat.dev}:${stat.ino}`;
    } catch {
      return [];
    }
    let fd: number;
    try {
      fd = openSync(this.path, "r");
    } catch {
      return [];
    }
    try {
      // A replaced file can reuse the inode, so its first line identifies it too.
      const head = this.readHead(fd);
      if (identity !== this.identity || head !== this.head || size < this.offset) {
        this.identity = identity;
        this.head = head;
        this.offset = 0;
      }
      const lines: string[] = [];
      let pending = Buffer.alloc(0);
      let position = this.offset;
      while (position < size) {
        const chunk = Buffer.alloc(Math.min(CHUNK, size - position));
        const read = readSync(fd, chunk, 0, chunk.length, position);
        if (read === 0) break;
        position += read;
        pending = Buffer.concat([pending, chunk.subarray(0, read)]);
        let newline: number;
        while ((newline = pending.indexOf(0x0a)) !== -1) {
          lines.push(pending.subarray(0, newline).toString("utf8"));
          this.offset += newline + 1;
          pending = pending.subarray(newline + 1);
        }
      }
      return lines;
    } finally {
      closeSync(fd);
      this.fresh = false;
    }
  }

  private readHead(fd: number): string {
    const buffer = Buffer.alloc(HEAD_BYTES);
    const read = readSync(fd, buffer, 0, HEAD_BYTES, 0);
    const end = buffer.subarray(0, read).indexOf(0x0a);
    return createHash("sha256").update(buffer.subarray(0, end === -1 ? read : end + 1)).digest("hex");
  }
}

function count(source: Record<string, unknown>, key: string): number {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** Feed a ledger from the Claude transcripts named in hook inputs. */
export class TranscriptUsage {
  private tails = new Map<string, JsonlTail>();

  constructor(private readonly ledger: UsageLedger) {}

  /** Remember the transcripts a hook input names. */
  note(input: Record<string, unknown>): void {
    for (const key of PATH_KEYS) {
      const path = input[key];
      if (typeof path === "string" && path && !this.tails.has(path)) {
        this.tails.set(path, new JsonlTail(path));
      }
    }
  }

  /** Read every known transcript's new lines into the ledger. */
  refresh(): void {
    for (const tail of this.tails.values()) {
      let first = tail.fresh;
      for (const raw of tail.read()) {
        if (first && raw.includes('"compact_boundary"')) {
          // The file starts after a compaction: usage before it is gone.
          this.ledger.markIncomplete();
        }
        first = false;
        if (raw.includes('"usage"')) this.record(raw);
      }
    }
  }

  private record(raw: string): void {
    let line: Record<string, any>;
    try {
      line = JSON.parse(raw);
    } catch {
      return;
    }
    const message = line?.message;
    if (line?.type !== "assistant" || !message || typeof message !== "object") return;
    const usage = message.usage;
    const key = line.requestId ?? message.id;
    if (!usage || typeof usage !== "object" || !key || message.model === "<synthetic>") return;
    const cacheRead = count(usage, "cache_read_input_tokens");
    const cacheWrite = count(usage, "cache_creation_input_tokens");
    this.ledger.record(
      `claude:${key}`,
      typeof message.model === "string" ? message.model : null,
      {
        input_tokens: count(usage, "input_tokens") + cacheRead + cacheWrite,
        output_tokens: count(usage, "output_tokens"),
        cache_read_tokens: cacheRead,
        cache_write_tokens: cacheWrite,
      },
      { source: TRANSCRIPT_SOURCE, complete: message.stop_reason !== null && message.stop_reason !== undefined },
    );
  }
}
