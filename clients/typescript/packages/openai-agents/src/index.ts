/**
 * `@agentegrity/openai-agents` — zero-config adapter for the OpenAI
 * Agents JS SDK (`@openai/agents`).
 *
 * The JS SDK reports lifecycle through events on the `Runner`, not through
 * a hooks option, so the adapter listens on the runner you run with:
 *
 * ```ts
 * import { Agent, Runner } from "@openai/agents";
 * import { instrument, report } from "@agentegrity/openai-agents";
 *
 * const runner = instrument(new Runner());
 * await runner.run(agent, "hello");
 * console.log(await report());
 * ```
 *
 * Event mapping:
 *
 *   agent_start       -> user_prompt_submit (and seeds the topology)
 *   agent_tool_start  -> pre_tool_use (with the parsed tool arguments)
 *   agent_tool_end    -> post_tool_use
 *   agent_handoff     -> subagent_start (and grows the topology)
 *   agent_end         -> stop
 *
 * Token usage comes from the run's shared `Usage`, one entry per model
 * request (`requestUsageEntries`). Agents run as tools share their parent's
 * `Usage`, so their requests are counted too, attributed to the agent
 * whose event first saw them.
 */

import { randomUUID } from "node:crypto";
import {
  AgentMember,
  AgentRole,
  AgentTopology,
  TopologyKind,
  createDefaultAdapter,
  type AgentProfile,
  type DefaultAdapter,
  type SessionExporter,
  type SessionSummary,
  type TokenUsage,
} from "@agentegrity/client";

const ADAPTER_NAME = "openai_agents";

/** The part of the SDK `Runner` the adapter needs. */
export interface RunnerLike {
  on(event: string, listener: (...args: any[]) => void): unknown;
  config?: { model?: unknown };
}

export interface InstrumentOptions {
  profile?: Partial<AgentProfile>;
}

interface UsageEntry {
  inputTokens?: number;
  outputTokens?: number;
  inputTokensDetails?: Record<string, number> | Array<Record<string, number>>;
  outputTokensDetails?: Record<string, number> | Array<Record<string, number>>;
}

interface SdkUsage extends UsageEntry {
  requests?: number;
  requestUsageEntries?: UsageEntry[];
}

function nameOf(agent: unknown): string {
  const a = agent as { name?: string; id?: string } | null;
  return String(a?.name ?? a?.id ?? "agent");
}

function modelOf(holder: unknown): string | null {
  const model = (holder as { model?: unknown } | null)?.model;
  return typeof model === "string" && model ? model : null;
}

function parseArguments(details: unknown): unknown {
  const raw = (details as { toolCall?: { arguments?: unknown } } | null)?.toolCall?.arguments;
  if (typeof raw !== "string") return raw ?? {};
  try {
    return JSON.parse(raw);
  } catch {
    return { raw };
  }
}

function sumDetail(details: UsageEntry["inputTokensDetails"], ...keys: string[]): number {
  const records = Array.isArray(details) ? details : details ? [details] : [];
  let total = 0;
  for (const record of records) {
    for (const key of keys) total += typeof record[key] === "number" ? record[key]! : 0;
  }
  return total;
}

/** Normalize SDK usage; OpenAI input counts already include cached tokens. */
function normalize(entry: UsageEntry, requests: number | null = 1): TokenUsage {
  return {
    input_tokens: entry.inputTokens ?? 0,
    output_tokens: entry.outputTokens ?? 0,
    cache_read_tokens: sumDetail(entry.inputTokensDetails, "cached_tokens", "cached_input_tokens"),
    cache_write_tokens: sumDetail(entry.inputTokensDetails, "cache_write_tokens"),
    reasoning_tokens: sumDetail(entry.outputTokensDetails, "reasoning_tokens"),
    requests,
  };
}

async function seedTopologyFromInitial(ad: DefaultAdapter, agentId: string): Promise<void> {
  if (ad.topology !== null && ad.topology.member(agentId) !== null) return;
  if (ad.topology !== null) return addHandoffTarget(ad, agentId);
  await ad.setTopology(
    new AgentTopology({
      kind: TopologyKind.PEER_TO_PEER,
      members: [new AgentMember({ agentId, name: agentId, role: AgentRole.PEER, capabilities: ["tool_use"] })],
      commChannels: new Set(["peer_messages"]),
    }),
    AgentRole.PEER,
  );
}

async function addHandoffTarget(ad: DefaultAdapter, agentId: string): Promise<void> {
  const existing = ad.topology;
  if (existing === null) return seedTopologyFromInitial(ad, agentId);
  if (existing.member(agentId) !== null) return;
  await ad.setTopology(
    existing.withMember(
      new AgentMember({ agentId, name: agentId, role: AgentRole.PEER, capabilities: ["tool_use"] }),
    ),
    AgentRole.PEER,
  );
}

/**
 * Per-adapter state: events are applied in the order the SDK emits them,
 * even though the SDK does not await listeners.
 */
class Session {
  private queue: Promise<void> = Promise.resolve();
  private runKeys = new WeakMap<object, { key: string; counted: number }>();

  constructor(readonly ad: DefaultAdapter) {}

  enqueue(work: () => Promise<void>): void {
    this.queue = this.queue.then(work).catch((err: unknown) => {
      // eslint-disable-next-line no-console
      console.warn(`[agentegrity:${ADAPTER_NAME}] event handling failed:`, err);
    });
  }

  settled(): Promise<void> {
    return this.queue;
  }

  /** Record requests the run's shared Usage gained since the last event. */
  recordUsage(context: unknown, model: string | null): void {
    const usage = (context as { usage?: SdkUsage } | null)?.usage;
    if (!usage || typeof usage !== "object") return;
    let run = this.runKeys.get(usage);
    if (!run) this.runKeys.set(usage, (run = { key: randomUUID(), counted: 0 }));
    const entries = usage.requestUsageEntries;
    if (!Array.isArray(entries)) {
      // Older SDKs keep only the running total; it replaces itself under one key.
      this.ad.recordUsage(`openai-agents:${run.key}:total`, model, normalize(usage, usage.requests ?? null), {
        source: "provider_response",
      });
      return;
    }
    for (let i = run.counted; i < entries.length; i++) {
      this.ad.recordUsage(`openai-agents:${run.key}:${i}`, model, normalize(entries[i]!), {
        source: "provider_response",
      });
    }
    run.counted = entries.length;
  }
}

const sessions = new WeakMap<DefaultAdapter, Session>();
const instrumented = new WeakMap<object, DefaultAdapter>();
let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) _default = createDefaultAdapter({ adapterName: ADAPTER_NAME });
  return _default;
}

function sessionFor(ad: DefaultAdapter): Session {
  let session = sessions.get(ad);
  if (!session) sessions.set(ad, (session = new Session(ad)));
  return session;
}

/**
 * Listen on a `Runner`'s lifecycle events. Returns the runner. Instrumenting
 * the same runner again is a no-op. Runs made with the SDK's `run()` helper
 * use an internal runner, so create a `Runner` to instrument.
 */
export function instrument<R extends RunnerLike>(runner: R, options: InstrumentOptions = {}): R {
  if (instrumented.has(runner)) return runner;
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: ADAPTER_NAME, profile: options.profile })
    : defaultAdapter();
  instrumented.set(runner, ad);
  const session = sessionFor(ad);
  const model = (agent: unknown) => modelOf(agent) ?? modelOf(runner.config);

  runner.on("agent_start", (_ctx: unknown, agent: unknown) => {
    session.enqueue(async () => {
      await seedTopologyFromInitial(ad, nameOf(agent));
      await ad.emit({ event_type: "user_prompt_submit", data: { agent: nameOf(agent) } });
    });
  });
  runner.on("agent_tool_start", (ctx: unknown, agent: unknown, tool: unknown, details: unknown) => {
    session.recordUsage(ctx, model(agent));
    session.enqueue(() =>
      ad.emit({
        event_type: "pre_tool_use",
        data: { tool_name: nameOf(tool), tool_input: parseArguments(details) },
      }),
    );
  });
  runner.on("agent_tool_end", (_ctx: unknown, _agent: unknown, tool: unknown, result: unknown) => {
    session.enqueue(() =>
      ad.emit({ event_type: "post_tool_use", data: { tool_name: nameOf(tool), tool_response: result } }),
    );
  });
  runner.on("agent_handoff", (ctx: unknown, fromAgent: unknown, toAgent: unknown) => {
    session.recordUsage(ctx, model(fromAgent));
    const toId = nameOf(toAgent);
    session.enqueue(async () => {
      await addHandoffTarget(ad, toId);
      await ad.emit({ event_type: "subagent_start", data: { agent_id: toId, handoff_from: nameOf(fromAgent) } });
    });
  });
  runner.on("agent_end", (ctx: unknown, agent: unknown, output: unknown) => {
    session.recordUsage(ctx, model(agent));
    session.enqueue(() => ad.emit({ event_type: "stop", data: { output } }));
  });
  return runner;
}

/** Wait until every event received so far has been handled. */
export async function flush(): Promise<void> {
  if (_default !== null) await sessionFor(_default).settled();
}

export async function report(): Promise<SessionSummary> {
  if (_default === null) {
    return {
      adapter: ADAPTER_NAME,
      agent_id: null,
      evaluations: 0,
      events: 0,
      attestation_records: 0,
      chain_hash_linked: true,
      enforce_mode: false,
    };
  }
  await flush();
  return _default.getSummary();
}

export function reset(): void {
  _default = null;
}

export function registerExporter(exporter: SessionExporter): void {
  defaultAdapter().registerExporter(exporter);
}

export function adapter(): DefaultAdapter {
  return defaultAdapter();
}

export type { SessionExporter };
