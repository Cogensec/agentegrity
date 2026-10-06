/**
 * `@agentegrity/vercel-ai` — zero-config adapter for the Vercel AI SDK (`ai`).
 * TypeScript-native addition with no Python equivalent.
 *
 * AI SDK 3.4 to 6 report through an OpenTelemetry tracer passed per call:
 *
 * ```ts
 * import { generateText } from "ai";
 * import { instrument, report } from "@agentegrity/vercel-ai";
 *
 * await generateText({ ...opts, experimental_telemetry: instrument() });
 * console.log(await report());
 * ```
 *
 * AI SDK 7 removed the tracer option; register the integration once:
 *
 * ```ts
 * import { registerTelemetry } from "ai";
 * import { telemetry } from "@agentegrity/vercel-ai";
 *
 * registerTelemetry(telemetry());
 * ```
 *
 * Mapping (tracer spans / v7 callbacks):
 *
 *   ai.generateText, ai.streamText, ai.generateObject, ai.streamObject
 *     start / onStart                  -> user_prompt_submit
 *     end / onEnd                      -> stop
 *   ai.toolCall start / onToolExecutionStart -> pre_tool_use
 *   ai.toolCall end / onToolExecutionEnd     -> post_tool_use or post_tool_use_failure
 *   *.doGenerate, *.doStream end / onLanguageModelCallEnd -> token usage
 *
 * Usage is counted per model call only; the operation spans and `onEnd`
 * carry the sum of their calls and would count it twice.
 */

import { randomUUID } from "node:crypto";
import {
  createDefaultAdapter,
  type AgentProfile,
  type DefaultAdapter,
  type EventType,
  type SessionExporter,
  type SessionSummary,
  type TokenUsage,
} from "@agentegrity/client";

const ADAPTER_NAME = "vercel_ai";
const OPERATIONS = new Set(["ai.generateText", "ai.streamText", "ai.generateObject", "ai.streamObject"]);
const MODEL_CALL = /\.(doGenerate|doStream)$/;

type Attributes = Record<string, unknown>;

export interface InstrumentOptions {
  profile?: Partial<AgentProfile>;
  /** If set, also emit generic events for spans the adapter does not map. */
  catchAll?: boolean;
}

/** Applies events in the order the SDK reports them; the SDK does not await us. */
class Session {
  private queue: Promise<void> = Promise.resolve();

  constructor(readonly ad: DefaultAdapter) {}

  emit(event_type: EventType, data: Record<string, unknown>): void {
    this.queue = this.queue
      .then(() => this.ad.emit({ event_type, data }))
      .catch((err: unknown) => {
        // eslint-disable-next-line no-console
        console.warn(`[agentegrity:${ADAPTER_NAME}] event handling failed:`, err);
      });
  }

  settled(): Promise<void> {
    return this.queue;
  }
}

function parseJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function num(attrs: Attributes, key: string): number | undefined {
  const value = attrs[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/**
 * Normalize a model-call span's usage across SDK majors:
 * v6 reports input totals with cache details; v5 streams report
 * inputTokens / cachedInputTokens; v3, v4 and v5 doGenerate report
 * promptTokens / completionTokens. Anthropic's input excluded the cache
 * before v6, so it is added back from what the span carries.
 */
function spanUsage(attrs: Attributes): { usage: TokenUsage; complete: boolean } | null {
  const reasoning = num(attrs, "ai.usage.outputTokenDetails.reasoningTokens") ?? num(attrs, "ai.usage.reasoningTokens");
  if (num(attrs, "ai.usage.inputTokenDetails.noCacheTokens") !== undefined ||
      num(attrs, "ai.usage.inputTokenDetails.cacheReadTokens") !== undefined) {
    return {
      usage: {
        input_tokens: num(attrs, "ai.usage.inputTokens") ?? 0,
        output_tokens: num(attrs, "ai.usage.outputTokens") ?? 0,
        cache_read_tokens: num(attrs, "ai.usage.inputTokenDetails.cacheReadTokens") ?? null,
        cache_write_tokens: num(attrs, "ai.usage.inputTokenDetails.cacheWriteTokens") ?? null,
        reasoning_tokens: reasoning ?? null,
      },
      complete: true,
    };
  }
  let input = num(attrs, "ai.usage.inputTokens") ?? num(attrs, "ai.usage.promptTokens");
  const output = num(attrs, "ai.usage.outputTokens") ?? num(attrs, "ai.usage.completionTokens");
  if (input === undefined && output === undefined) return null;
  let cacheRead = num(attrs, "ai.usage.cachedInputTokens");
  let cacheWrite: number | undefined;
  let complete = true;
  if (String(attrs["ai.model.provider"] ?? "").startsWith("anthropic")) {
    const meta = (parseJson(attrs["ai.response.providerMetadata"]) as { anthropic?: Attributes } | null)?.anthropic ?? {};
    cacheWrite = num(meta, "cacheCreationInputTokens");
    cacheRead ??= num(meta, "cacheReadInputTokens");
    // v5 generate spans drop the cache-read count, so the input is a lower bound.
    if (cacheRead === undefined) complete = false;
    input = (input ?? 0) + (cacheRead ?? 0) + (cacheWrite ?? 0);
  }
  return {
    usage: {
      input_tokens: input ?? 0,
      output_tokens: output ?? 0,
      cache_read_tokens: cacheRead ?? null,
      cache_write_tokens: cacheWrite ?? null,
      reasoning_tokens: reasoning ?? null,
    },
    complete,
  };
}

function modelOf(attrs: Attributes): string | null {
  for (const key of ["ai.response.model", "gen_ai.response.model", "ai.model.id", "gen_ai.request.model"]) {
    if (typeof attrs[key] === "string" && attrs[key]) return attrs[key] as string;
  }
  return null;
}

/** OTel-compatible span: collects attributes and reports when the SDK ends it. */
class AgentegritySpan {
  private readonly attributes: Attributes;
  private error: string | null = null;
  private ended = false;

  constructor(
    private readonly name: string,
    attributes: Attributes | undefined,
    private readonly session: Session,
    private readonly catchAll: boolean,
  ) {
    this.attributes = { ...(attributes ?? {}) };
    if (OPERATIONS.has(name)) {
      session.emit("user_prompt_submit", { operation: name, prompt: parseJson(this.attributes["ai.prompt"]) ?? null });
    } else if (name === "ai.toolCall") {
      session.emit("pre_tool_use", {
        tool_name: this.attributes["ai.toolCall.name"] ?? "unknown",
        tool_input: parseJson(this.attributes["ai.toolCall.args"]) ?? {},
      });
    } else if (catchAll && !MODEL_CALL.test(name)) {
      session.emit("user_prompt_submit", { span: name });
    }
  }

  setAttribute(key: string, value: unknown): this {
    this.attributes[key] = value;
    return this;
  }

  setAttributes(attrs: Attributes): this {
    Object.assign(this.attributes, attrs);
    return this;
  }

  addEvent(): this {
    return this;
  }

  setStatus(): this {
    return this;
  }

  recordException(err: unknown): this {
    // OTel exceptions are Errors or plain { message } objects.
    const message = (err as { message?: unknown } | null)?.message;
    this.error = typeof message === "string" ? message : String(err);
    return this;
  }

  updateName(): this {
    return this;
  }

  isRecording(): boolean {
    return !this.ended;
  }

  spanContext(): { traceId: string; spanId: string; traceFlags: number } {
    return { traceId: "0".repeat(32), spanId: "0".repeat(16), traceFlags: 0 };
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    const a = this.attributes;
    if (MODEL_CALL.test(this.name)) {
      const counted = spanUsage(a);
      if (counted) {
        this.session.ad.recordUsage(`vercel-ai:${randomUUID()}`, modelOf(a), counted.usage, {
          source: "trace",
          complete: counted.complete,
        });
      }
    } else if (this.name === "ai.toolCall") {
      const tool_name = a["ai.toolCall.name"] ?? "unknown";
      if (this.error !== null) this.session.emit("post_tool_use_failure", { tool_name, error: this.error });
      else this.session.emit("post_tool_use", { tool_name, tool_response: parseJson(a["ai.toolCall.result"]) ?? null });
    } else if (OPERATIONS.has(this.name)) {
      this.session.emit("stop", { operation: this.name });
    } else if (this.catchAll) {
      this.session.emit("stop", { span: this.name });
    }
  }
}

/** The OTel Tracer surface the AI SDK calls. The SDK ends every span itself. */
class AgentegrityTracer {
  constructor(
    private readonly session: Session,
    private readonly catchAll: boolean,
  ) {}

  startSpan(name: string, options?: { attributes?: Attributes }): AgentegritySpan {
    return new AgentegritySpan(name, options?.attributes, this.session, this.catchAll);
  }

  /** OTel overloads: (name, fn), (name, options, fn), (name, options, context, fn). */
  startActiveSpan<T>(name: string, ...args: unknown[]): T {
    const fn = args[args.length - 1] as (span: AgentegritySpan) => T;
    const options = args.length > 1 ? (args[0] as { attributes?: Attributes } | undefined) : undefined;
    return fn(this.startSpan(name, options));
  }
}

const sessions = new WeakMap<DefaultAdapter, Session>();
let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) _default = createDefaultAdapter({ adapterName: ADAPTER_NAME });
  return _default;
}

function sessionFor(options: { profile?: Partial<AgentProfile> }): Session {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: ADAPTER_NAME, profile: options.profile })
    : defaultAdapter();
  let session = sessions.get(ad);
  if (!session) sessions.set(ad, (session = new Session(ad)));
  return session;
}

/**
 * Build an `experimental_telemetry` object for AI SDK 3.4 to 6. Pass it to
 * `generateText`, `streamText`, `generateObject` or `streamObject`.
 */
export function instrument(options: InstrumentOptions = {}): { isEnabled: true; tracer: AgentegrityTracer } {
  return { isEnabled: true, tracer: new AgentegrityTracer(sessionFor(options), options.catchAll ?? false) };
}

type V7Usage = {
  inputTokens?: number;
  outputTokens?: number;
  inputTokenDetails?: { cacheReadTokens?: number; cacheWriteTokens?: number };
  outputTokenDetails?: { reasoningTokens?: number };
};

/**
 * A telemetry integration for AI SDK 7. Register it once with
 * `registerTelemetry(telemetry())`; passing it per call through
 * `telemetry.integrations` replaces any globally registered integrations.
 */
export function telemetry(options: { profile?: Partial<AgentProfile> } = {}) {
  const session = sessionFor(options);
  return {
    onStart(event: { operationId?: string; prompt?: unknown }) {
      if (OPERATIONS.has(String(event.operationId))) {
        session.emit("user_prompt_submit", { operation: event.operationId, prompt: event.prompt ?? null });
      }
    },
    onToolExecutionStart(event: { toolCall?: { toolName?: string; input?: unknown } }) {
      session.emit("pre_tool_use", { tool_name: event.toolCall?.toolName ?? "unknown", tool_input: event.toolCall?.input ?? {} });
    },
    onToolExecutionEnd(event: { toolCall?: { toolName?: string }; toolOutput?: { type?: string; output?: unknown; error?: unknown } }) {
      const tool_name = event.toolCall?.toolName ?? "unknown";
      if (event.toolOutput?.type === "tool-error") {
        const error = event.toolOutput.error;
        session.emit("post_tool_use_failure", { tool_name, error: error instanceof Error ? error.message : String(error) });
      } else {
        session.emit("post_tool_use", { tool_name, tool_response: event.toolOutput?.output ?? null });
      }
    },
    onLanguageModelCallEnd(event: { responseId?: string; modelId?: string; usage?: V7Usage }) {
      const u = event.usage;
      if (!u) return;
      session.ad.recordUsage(`vercel-ai:${event.responseId || randomUUID()}`, event.modelId ?? null, {
        input_tokens: u.inputTokens ?? 0,
        output_tokens: u.outputTokens ?? 0,
        cache_read_tokens: u.inputTokenDetails?.cacheReadTokens ?? null,
        cache_write_tokens: u.inputTokenDetails?.cacheWriteTokens ?? null,
        reasoning_tokens: u.outputTokenDetails?.reasoningTokens ?? null,
      }, { source: "provider_response" });
    },
    onEnd(event: { operationId?: string }) {
      if (event.operationId === undefined || OPERATIONS.has(event.operationId)) {
        session.emit("stop", { operation: event.operationId ?? null });
      }
    },
  };
}

/** Wait until every event received so far has been handled. */
export async function flush(): Promise<void> {
  if (_default !== null) await sessionFor({}).settled();
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

export { AgentegrityTracer, AgentegritySpan };
export type { SessionExporter };
