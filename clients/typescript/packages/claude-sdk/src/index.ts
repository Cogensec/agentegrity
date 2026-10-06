/**
 * `@agentegrity/claude-sdk` — zero-config adapter for the Claude Agent
 * SDK (`@anthropic-ai/claude-agent-sdk`).
 *
 * ```ts
 * import { query } from "@anthropic-ai/claude-agent-sdk";
 * import { hooks, observe, report } from "@agentegrity/claude-sdk";
 *
 * for await (const message of query({ prompt, options: { hooks: hooks() } })) {
 *   observe(message); // optional: exact token totals
 * }
 * console.log(await report());
 * ```
 *
 * Event mapping (SDK hook -> agentegrity event):
 *
 *   UserPromptSubmit    -> user_prompt_submit
 *   PreToolUse          -> pre_tool_use
 *   PostToolUse         -> post_tool_use
 *   PostToolUseFailure  -> post_tool_use_failure
 *   SubagentStart       -> subagent_start
 *   SubagentStop        -> subagent_stop
 *   PreCompact          -> pre_compact
 *   Stop                -> stop
 *
 * Token usage is read from the session transcript the hooks name, and from
 * each subagent's transcript, with no code change. Passing each message to
 * {@link observe} replaces that estimate with the SDK's own per-model totals.
 */

import type { HookCallback, HookCallbackMatcher, HookEvent } from "@anthropic-ai/claude-agent-sdk";
import {
  createDefaultAdapter,
  type AdapterConfig,
  type AgentProfile,
  type DefaultAdapter,
  type EventType,
  type SessionExporter,
  type SessionSummary,
  type TokenUsage,
} from "@agentegrity/client";
import { TRANSCRIPT_SOURCE, TranscriptUsage } from "./transcript.js";

const ADAPTER_NAME = "claude";

export interface HooksOptions {
  profile?: Partial<AgentProfile>;
}

/** Usage state per adapter: the transcript reader until `observe()` takes over. */
interface UsageState {
  transcripts: TranscriptUsage | null;
  generation: number;
}

const usageStates = new WeakMap<DefaultAdapter, UsageState>();
let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) _default = createDefaultAdapter({ adapterName: ADAPTER_NAME });
  return _default;
}

function usageState(ad: DefaultAdapter): UsageState {
  let state = usageStates.get(ad);
  if (!state) usageStates.set(ad, (state = { transcripts: new TranscriptUsage(ad.usage), generation: 0 }));
  return state;
}

type Input = Record<string, unknown>;

/** Which hook input fields each event forwards. */
const EVENTS: Array<[HookEvent, EventType, (input: Input) => Record<string, unknown>]> = [
  ["UserPromptSubmit", "user_prompt_submit", (i) => ({ prompt: i.prompt })],
  ["PreToolUse", "pre_tool_use", (i) => ({ tool_name: i.tool_name, tool_input: i.tool_input })],
  ["PostToolUse", "post_tool_use", (i) => ({ tool_name: i.tool_name, tool_response: i.tool_response })],
  ["PostToolUseFailure", "post_tool_use_failure", (i) => ({ tool_name: i.tool_name, error: i.error })],
  ["SubagentStart", "subagent_start", (i) => ({ agent_id: i.agent_id, agent_type: i.agent_type })],
  ["SubagentStop", "subagent_stop", (i) => ({ agent_id: i.agent_id, agent_type: i.agent_type })],
  ["PreCompact", "pre_compact", (i) => ({ trigger: i.trigger })],
  ["Stop", "stop", (i) => ({ output: i.last_assistant_message ?? "" })],
];

function callback(ad: DefaultAdapter, eventType: EventType, fields: (input: Input) => Record<string, unknown>): HookCallback {
  return async (hookInput) => {
    const input = hookInput as unknown as Input;
    const state = usageState(ad);
    state.transcripts?.note(input);
    if (eventType === "stop") state.transcripts?.refresh();
    await ad.emit({ event_type: eventType, data: fields(input) });
    return {};
  };
}

/**
 * Hooks for `Options.hooks`: each event maps to one matcher whose callback
 * forwards the hook input and lets the SDK continue.
 */
export function hooks(options: HooksOptions = {}): Partial<Record<HookEvent, HookCallbackMatcher[]>> {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: ADAPTER_NAME, profile: options.profile })
    : defaultAdapter();
  return Object.fromEntries(
    EVENTS.map(([hook, eventType, fields]) => [hook, [{ hooks: [callback(ad, eventType, fields)] }]]),
  );
}

function modelUsage(usage: Record<string, unknown>): TokenUsage {
  const count = (key: string) => (typeof usage[key] === "number" ? (usage[key] as number) : 0);
  const cacheRead = count("cacheReadInputTokens");
  const cacheWrite = count("cacheCreationInputTokens");
  return {
    // The CLI's input count leaves the cache out; thinking is already inside output.
    input_tokens: count("inputTokens") + cacheRead + cacheWrite,
    output_tokens: count("outputTokens"),
    cache_read_tokens: cacheRead,
    cache_write_tokens: cacheWrite,
    reasoning_tokens: typeof usage.thinkingTokens === "number" ? usage.thinkingTokens : null,
    requests: null,
  };
}

/**
 * Record exact token usage from a message the SDK yielded (optional).
 *
 * A `result` message's `modelUsage` holds the session's running totals per
 * model, subagents included; the first one replaces the transcript estimate
 * for the rest of the session. A `conversation_reset` zeroes the SDK's
 * totals, so the totals before it are kept.
 */
export function observe(message: unknown, ad: DefaultAdapter = defaultAdapter()): void {
  const m = message as { type?: string; modelUsage?: Record<string, unknown> } | null;
  const state = usageState(ad);
  if (m?.type === "conversation_reset") {
    state.generation++;
    return;
  }
  if (m?.type !== "result" || !m.modelUsage || typeof m.modelUsage !== "object") return;
  if (state.transcripts !== null) {
    state.transcripts = null;
    ad.usage.discard(TRANSCRIPT_SOURCE);
  }
  for (const [model, usage] of Object.entries(m.modelUsage)) {
    if (usage && typeof usage === "object") {
      ad.recordUsage(`result:${state.generation}:${model}`, model, modelUsage(usage as Record<string, unknown>), {
        source: "provider_response",
      });
    }
  }
}

/** Snapshot of the current session summary. */
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
  return _default.getSummary();
}

export function reset(): void {
  _default = null;
}

export function registerExporter(exporter: SessionExporter): void {
  defaultAdapter().registerExporter(exporter);
}

/** Escape hatch: direct access to the module-global adapter. */
export function adapter(): DefaultAdapter {
  return defaultAdapter();
}

export type { AdapterConfig, DefaultAdapter, SessionExporter };
