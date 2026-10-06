/**
 * Token usage per session, normalized across frameworks. Mirrors the Python
 * `agentegrity.core.usage` module; both are pinned to the same test vectors.
 *
 * Frameworks disagree on whether "input tokens" include cached tokens, so
 * every package records one shape:
 *
 * - `input_tokens` is every input token processed, cached or not.
 *   `cache_read_tokens` / `cache_write_tokens` are parts of it.
 * - `output_tokens` is every generated token; `reasoning_tokens` is part of it.
 *
 * Breakdown fields are left out when no source reported them, so a missing
 * number never reads as zero. Entries are keyed per model call and a repeated
 * key replaces the earlier entry, which is how repeated reports and running
 * totals are counted once.
 */

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  reasoning_tokens?: number | null;
  /** Model calls this entry counts. Defaults to 1; null when a running total has no count. */
  requests?: number | null;
}

export type UsageSource = "transcript" | "provider_response" | "trace";

export interface RecordOptions {
  source: UsageSource | string;
  /** False when the counts are known to be partial. Defaults to true. */
  complete?: boolean;
}

export const UNKNOWN_MODEL = "unknown";

const BREAKDOWN = ["cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "requests"] as const;

interface Entry {
  model: string;
  usage: TokenUsage;
  source: string;
  complete: boolean;
}

type Totals = Required<Pick<TokenUsage, "input_tokens" | "output_tokens">> &
  Partial<Record<(typeof BREAKDOWN)[number], number>>;

function add(left: number | undefined, right: number | null | undefined): number | undefined {
  if (right === null || right === undefined) return left;
  return (left ?? 0) + right;
}

function accumulate(total: Totals, usage: TokenUsage): Totals {
  const next: Totals = {
    input_tokens: total.input_tokens + usage.input_tokens,
    output_tokens: total.output_tokens + usage.output_tokens,
  };
  for (const name of BREAKDOWN) {
    const value = name === "requests" ? (usage.requests === undefined ? 1 : usage.requests) : usage[name];
    const sum = add(total[name], value);
    if (sum !== undefined) next[name] = sum;
  }
  return next;
}

function serialize(totals: Totals): Record<string, number> {
  return { ...totals, total_tokens: totals.input_tokens + totals.output_tokens };
}

/** Running per-model token totals for one session. */
export class UsageLedger {
  private entries = new Map<string, Entry>();
  private gap = false;

  /** Record one call's usage; a key seen before is replaced, not added. */
  record(key: string, model: string | null | undefined, usage: TokenUsage, options: RecordOptions): void {
    this.entries.set(key, {
      model: model || UNKNOWN_MODEL,
      usage,
      source: options.source,
      complete: options.complete ?? true,
    });
  }

  /** Drop every entry from one source, when a more exact source supersedes it. */
  discard(source: string): void {
    for (const [key, entry] of this.entries) {
      if (entry.source === source) this.entries.delete(key);
    }
  }

  /** Flag a known gap: calls happened that no source can account for. */
  markIncomplete(): void {
    this.gap = true;
  }

  /** Totals overall and per model, or null when nothing was recorded. */
  toDict(): Record<string, unknown> | null {
    if (this.entries.size === 0) return null;
    const zero: Totals = { input_tokens: 0, output_tokens: 0 };
    const byModel = new Map<string, Totals>();
    let total = zero;
    for (const entry of this.entries.values()) {
      byModel.set(entry.model, accumulate(byModel.get(entry.model) ?? zero, entry.usage));
      total = accumulate(total, entry.usage);
    }
    const entries = [...this.entries.values()];
    return {
      ...serialize(total),
      complete: !this.gap && entries.every((e) => e.complete),
      sources: [...new Set(entries.map((e) => e.source))].sort(),
      by_model: Object.fromEntries(
        [...byModel.keys()].sort().map((model) => [model, serialize(byModel.get(model) as Totals)]),
      ),
    };
  }

  /** True when nothing has been recorded. */
  get empty(): boolean {
    return this.entries.size === 0;
  }
}
