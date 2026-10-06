/**
 * `@agentegrity/crewai`: zero-config adapter for CrewAI TypeScript
 * (`@crewai-ts/core`).
 *
 * ```ts
 * import { Crew, crewaiEventBus } from "@crewai-ts/core";
 * import { instrument, report } from "@agentegrity/crewai";
 *
 * const close = instrument(crewaiEventBus, { crew });
 * await crew.kickoff();
 * await close();
 * console.log(await report());
 * ```
 *
 * The adapter subscribes to CrewAI's event bus. Event mapping:
 *
 *   crew_kickoff_started       -> user_prompt_submit
 *   crew_kickoff_completed     -> stop
 *   crew_kickoff_failed        -> stop (with the error)
 *   task_started               -> task_started
 *   agent_execution_started    -> subagent_start
 *   agent_execution_completed  -> subagent_stop
 *   agent_execution_error      -> subagent_stop (with the error)
 *   tool_usage_started         -> pre_tool_use
 *   tool_usage_finished        -> post_tool_use
 *   tool_usage_error           -> post_tool_use_failure
 *   llm_call_completed         -> token usage (per call id)
 *
 * Python parity: mirrors `agentegrity.crewai`.
 */

import {
  AgentMember,
  AgentRole,
  AgentTopology,
  TopologyKind,
  createDefaultAdapter,
  type AgentProfile,
  type DefaultAdapter,
  type EventType,
  type SessionExporter,
  type SessionSummary,
  type TokenUsage,
} from "@agentegrity/client";

/**
 * Build and declare an AgentTopology from a CrewAI Crew (v0.8).
 *
 * crew.process === "sequential" → HUB_SPOKE with the first agent as
 * LEADER (structural convention for parent_id linkage, not crew
 * semantics). "hierarchical" → HIERARCHICAL_DAG with SUPERVISOR /
 * WORKER roles.
 *
 * Mirrors Python `agentegrity.crewai.CrewAIAdapter._declare_topology`.
 */
export function declareCrewTopology(crew: unknown, ad: DefaultAdapter): void {
  const c = (crew ?? {}) as { agents?: unknown[]; process?: string };
  const agents = Array.isArray(c.agents) ? c.agents : [];
  if (agents.length === 0) return;
  const process = String(c.process ?? "sequential").toLowerCase();
  const kind = process.includes("hierarch")
    ? TopologyKind.HIERARCHICAL_DAG
    : TopologyKind.HUB_SPOKE;

  const idOf = (a: unknown): string => {
    const x = (a ?? {}) as { role?: string; id?: string };
    return String(x.role ?? x.id ?? "agent");
  };

  const leaderId = idOf(agents[0]);
  const members: AgentMember[] = [];
  for (let i = 0; i < agents.length; i++) {
    const id = idOf(agents[i]);
    if (i === 0) {
      members.push(
        new AgentMember({
          agentId: id,
          name: id,
          role: AgentRole.LEADER,
          capabilities: ["tool_use"],
        }),
      );
    } else {
      members.push(
        new AgentMember({
          agentId: id,
          name: id,
          role: kind === TopologyKind.HUB_SPOKE ? AgentRole.MEMBER : AgentRole.WORKER,
          parentId: leaderId,
          capabilities: ["tool_use"],
        }),
      );
    }
  }
  const topology = new AgentTopology({
    kind,
    members,
    commChannels: new Set(["peer_messages"]),
  });
  void ad.setTopology(topology, AgentRole.LEADER);
}

type Usage = Record<string, unknown>;
type Handler = (source: any, event: any) => void;

/** The part of CrewAI's `EventBus` the adapter uses. */
export interface CrewAIEventBus {
  on(eventType: string, handler: Handler): () => void;
}

const ADAPTER_NAME = "crewai";

let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) _default = createDefaultAdapter({ adapterName: ADAPTER_NAME });
  return _default;
}

export interface InstrumentOptions {
  profile?: Partial<AgentProfile>;
  /**
   * The crew to be run. Its agents are declared as the session's
   * AgentTopology; without it the adapter stays single-agent.
   */
  crew?: unknown;
}

function num(usage: Usage, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = usage[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function nested(usage: Usage, parent: string, key: string): number | null {
  const details = usage[parent];
  return details && typeof details === "object" ? num(details as Usage, key) : null;
}

/**
 * Normalize one call's provider usage, as CrewAI forwards it. OpenAI and
 * Gemini prompt counts already include the cache; Anthropic reports cached
 * tokens beside its input count, and Gemini reports thinking beside output.
 */
function callUsage(usage: Usage): TokenUsage {
  const cacheRead = num(usage, "cached_prompt_tokens", "cache_read_input_tokens", "cached_content_token_count")
    ?? nested(usage, "prompt_tokens_details", "cached_tokens")
    ?? nested(usage, "input_tokens_details", "cached_tokens");
  const cacheWrite = num(usage, "cache_creation_tokens", "cache_creation_input_tokens");
  const anthropic = "cache_read_input_tokens" in usage || "cache_creation_input_tokens" in usage;
  const thoughts = num(usage, "thoughts_token_count");
  const input = num(usage, "prompt_tokens", "prompt_token_count", "input_tokens") ?? 0;
  return {
    input_tokens: anthropic ? input + (cacheRead ?? 0) + (cacheWrite ?? 0) : input,
    output_tokens: (num(usage, "completion_tokens", "candidates_token_count", "output_tokens") ?? 0) + (thoughts ?? 0),
    cache_read_tokens: cacheRead,
    cache_write_tokens: cacheWrite,
    reasoning_tokens: thoughts
      ?? num(usage, "reasoning_tokens")
      ?? nested(usage, "completion_tokens_details", "reasoning_tokens")
      ?? nested(usage, "output_tokens_details", "reasoning_tokens"),
  };
}

const TOTAL_FIELDS = [
  "promptTokens",
  "completionTokens",
  "cachedPromptTokens",
  "cacheCreationTokens",
  "reasoningTokens",
  "successfulRequests",
] as const;
type Totals = Record<(typeof TOTAL_FIELDS)[number], number>;

/** CrewAI's running `UsageMetrics` for one client, or null for per-call usage. */
function runningTotals(usage: Usage): Totals | null {
  if (typeof usage.successfulRequests !== "number") return null;
  return Object.fromEntries(TOTAL_FIELDS.map((k) => [k, num(usage, k) ?? 0])) as Totals;
}

/**
 * Per-adapter state. CrewAI calls handlers synchronously and does not await
 * them, so events are queued to reach the exporter in emission order.
 */
class Session {
  private queue: Promise<void> = Promise.resolve();
  /** The last running total seen per LLM client. */
  private readonly totals = new WeakMap<object, Totals>();
  /** Calls already counted from their own usage. */
  private readonly counted = new Set<string>();

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

  /**
   * Count one `llm_call_completed`. CrewAI reports a call twice under one
   * call id: the client's per-call provider usage, then the client's running
   * total. The running total stands in for calls that carried no usage of
   * their own (native tool calling), as the delta since the last total.
   */
  recordCall(source: unknown, event: { call_id?: unknown; event_id?: unknown; model?: unknown; usage?: unknown }): void {
    const usage = event.usage;
    if (!usage || typeof usage !== "object") return;
    const call = String(event.call_id ?? event.event_id ?? "");
    const key = `crewai:${call}`;
    const sourceModel = (source as { model?: unknown } | null)?.model;
    const model = typeof event.model === "string" ? event.model : typeof sourceModel === "string" ? sourceModel : null;
    const totals = runningTotals(usage as Usage);
    if (!totals) {
      this.counted.add(call);
      this.ad.recordUsage(key, model, callUsage(usage as Usage), { source: "provider_response" });
      return;
    }
    if (!source || typeof source !== "object") return;
    const last = this.totals.get(source);
    this.totals.set(source, totals);
    // A smaller total means the client's metrics were reset.
    const base = last && last.successfulRequests <= totals.successfulRequests ? last : null;
    const delta = (k: (typeof TOTAL_FIELDS)[number]) => totals[k] - (base?.[k] ?? 0);
    if (this.counted.has(call) || delta("successfulRequests") <= 0) return;
    const cacheRead = delta("cachedPromptTokens");
    this.ad.recordUsage(key, model, {
      // CrewAI tracks Anthropic input without the cache, as the provider reports it.
      input_tokens: delta("promptTokens") + ((source as { provider?: unknown }).provider === "anthropic" ? cacheRead : 0),
      output_tokens: delta("completionTokens"),
      cache_read_tokens: cacheRead,
      cache_write_tokens: delta("cacheCreationTokens"),
      reasoning_tokens: delta("reasoningTokens"),
    }, { source: "provider_response" });
  }
}

const sessions = new WeakMap<DefaultAdapter, Session>();
const subscriptions = new WeakMap<object, Map<DefaultAdapter, () => Promise<void>>>();

function sessionFor(ad: DefaultAdapter): Session {
  let session = sessions.get(ad);
  if (!session) sessions.set(ad, (session = new Session(ad)));
  return session;
}

function roleOf(agent: unknown): string {
  const a = (agent ?? {}) as { role?: unknown; id?: unknown };
  return String(a.role ?? a.id ?? "agent");
}

function text(value: unknown): unknown {
  const raw = (value as { raw?: unknown } | null)?.raw;
  return typeof raw === "string" ? raw : value;
}

/**
 * Subscribe to a CrewAI event bus (`crewaiEventBus` from `@crewai-ts/core`).
 * Instrumenting the same bus again returns the existing subscription.
 * Returns a function that unsubscribes and ends the session.
 */
export function instrument(bus: CrewAIEventBus, options: InstrumentOptions = {}): () => Promise<void> {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: ADAPTER_NAME, profile: options.profile })
    : defaultAdapter();
  let byAdapter = subscriptions.get(bus);
  if (!byAdapter) subscriptions.set(bus, (byAdapter = new Map()));
  const existing = byAdapter.get(ad);
  if (existing) return existing;

  if (options.crew !== undefined) declareCrewTopology(options.crew, ad);
  const session = sessionFor(ad);
  const emit = (event_type: EventType, data: Record<string, unknown>) =>
    session.enqueue(() => ad.emit({ event_type, data }));
  const toolName = (event: any) => String(event.tool_name ?? event.toolName ?? "unknown");
  const agentOf = (source: unknown, event: any) => roleOf(event.agent ?? source);

  const handlers: Record<string, Handler> = {
    crew_kickoff_started: (_source, event) => emit("user_prompt_submit", { prompt: event.inputs ?? {} }),
    crew_kickoff_completed: (_source, event) => emit("stop", { output: text(event.output) }),
    crew_kickoff_failed: (_source, event) => emit("stop", { error: String(event.error) }),
    task_started: (source, event) =>
      emit("task_started", {
        task_id: String(source?.id ?? event.task_id ?? ""),
        description: String(source?.description ?? event.taskDescription ?? ""),
        agent_id: source?.agent ? roleOf(source.agent) : "",
      }),
    agent_execution_started: (source, event) => emit("subagent_start", { agent_id: agentOf(source, event) }),
    agent_execution_completed: (source, event) => emit("subagent_stop", { agent_id: agentOf(source, event) }),
    agent_execution_error: (source, event) =>
      emit("subagent_stop", { agent_id: agentOf(source, event), error: String(event.error) }),
    tool_usage_started: (_source, event) =>
      emit("pre_tool_use", { tool_name: toolName(event), tool_input: event.tool_args ?? event.toolArgs ?? {} }),
    tool_usage_finished: (_source, event) =>
      emit("post_tool_use", { tool_name: toolName(event), tool_response: event.output }),
    tool_usage_error: (_source, event) =>
      emit("post_tool_use_failure", { tool_name: toolName(event), error: String(event.error) }),
    llm_call_completed: (source, event) => session.recordCall(source, event),
  };
  const unsubscribe = Object.entries(handlers).map(([type, handler]) => bus.on(type, handler));

  const close = async () => {
    if (byAdapter.get(ad) !== close) return;
    byAdapter.delete(ad);
    for (const off of unsubscribe) off();
    await session.settled();
    await ad.end();
  };
  byAdapter.set(ad, close);
  return close;
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
