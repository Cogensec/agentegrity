/**
 * `@agentegrity/vercel-ai` — zero-config adapter for the Vercel AI SDK
 * (`ai`). TypeScript-native addition with no Python equivalent.
 *
 * Vercel AI SDK accepts telemetry via an OpenTelemetry-compatible
 * tracer passed through `experimental_telemetry: { isEnabled: true,
 * tracer }`. This package ships a minimal tracer that implements just
 * enough of the OTel TracerProvider/Tracer/Span surface to accept
 * AI-SDK spans and forward them as agentegrity events — no
 * `@opentelemetry/api` runtime dependency required.
 *
 * Usage:
 *
 * ```ts
 * import { streamText } from "ai";
 * import { instrument, report } from "@agentegrity/vercel-ai";
 *
 * const { textStream } = streamText({
 *   ...opts,
 *   experimental_telemetry: instrument(),
 * });
 * console.log(await report());
 * ```
 *
 * Span-name mapping (as emitted by the AI SDK):
 *
 *   ai.generateText / ai.streamText → user_prompt_submit + stop
 *   ai.toolCall                      → pre_tool_use + post_tool_use
 *   ai.generateObject                → user_prompt_submit + stop
 */

import {
  createDefaultAdapter,
  type AgentProfile,
  type DefaultAdapter,
  type SessionExporter,
  type SessionSummary,
} from "@agentegrity/client";

let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) {
    _default = createDefaultAdapter({ adapterName: "vercel_ai" });
  }
  return _default;
}

export interface InstrumentOptions {
  profile?: Partial<AgentProfile>;
  enforce?: boolean;
  /** If set, also emit generic `stop` events for unknown span names. */
  catchAll?: boolean;
}

/**
 * Minimal Span implementation compatible with the methods the AI SDK
 * calls on spans returned from `tracer.startSpan(...)`.
 */
class AgentegritySpan {
  private attributes: Record<string, unknown> = {};
  private ended = false;

  constructor(
    private readonly name: string,
    private readonly adapter: DefaultAdapter,
    private readonly catchAll: boolean,
  ) {
    void this.onStart();
  }

  private async onStart(): Promise<void> {
    const mapped = mapStartSpan(this.name);
    if (mapped) {
      await this.adapter.emit({
        event_type: mapped,
        data: { span: this.name },
      });
    } else if (this.catchAll) {
      await this.adapter.emit({
        event_type: "user_prompt_submit",
        data: { span: this.name },
      });
    }
  }

  setAttribute(key: string, value: unknown): this {
    this.attributes[key] = value;
    return this;
  }

  setAttributes(attrs: Record<string, unknown>): this {
    Object.assign(this.attributes, attrs);
    return this;
  }

  addEvent(_name: string, _attrs?: Record<string, unknown>): this {
    return this;
  }

  setStatus(_status: { code: number; message?: string }): this {
    return this;
  }

  recordException(err: unknown): this {
    void this.adapter.emit({
      event_type: "post_tool_use_failure",
      data: {
        span: this.name,
        error: err instanceof Error ? err.message : String(err),
      },
    });
    return this;
  }

  updateName(_name: string): this {
    return this;
  }

  isRecording(): boolean {
    return !this.ended;
  }

  spanContext(): { traceId: string; spanId: string; traceFlags: number } {
    return { traceId: "0".repeat(32), spanId: "0".repeat(16), traceFlags: 0 };
  }

  end(_endTime?: number): void {
    if (this.ended) return;
    this.ended = true;
    const mapped = mapEndSpan(this.name);
    if (mapped) {
      void this.adapter.emit({
        event_type: mapped,
        data: { span: this.name, attributes: this.attributes },
      });
    } else if (this.catchAll) {
      void this.adapter.emit({
        event_type: "stop",
        data: { span: this.name, attributes: this.attributes },
      });
    }
  }
}

function mapStartSpan(
  name: string,
): Parameters<DefaultAdapter["emit"]>[0]["event_type"] | null {
  switch (name) {
    case "ai.generateText":
    case "ai.streamText":
    case "ai.generateObject":
    case "ai.streamObject":
      return "user_prompt_submit";
    case "ai.toolCall":
      return "pre_tool_use";
    default:
      return null;
  }
}

function mapEndSpan(
  name: string,
): Parameters<DefaultAdapter["emit"]>[0]["event_type"] | null {
  switch (name) {
    case "ai.generateText":
    case "ai.streamText":
    case "ai.generateObject":
    case "ai.streamObject":
      return "stop";
    case "ai.toolCall":
      return "post_tool_use";
    default:
      return null;
  }
}

class AgentegrityTracer {
  constructor(
    private readonly adapter: DefaultAdapter,
    private readonly catchAll: boolean,
  ) {}

  startSpan(name: string): AgentegritySpan {
    return new AgentegritySpan(name, this.adapter, this.catchAll);
  }

  startActiveSpan<T>(
    name: string,
    _optsOrFn: unknown,
    fnMaybe?: (span: AgentegritySpan) => T,
  ): T {
    const fn = (typeof _optsOrFn === "function" ? _optsOrFn : fnMaybe) as
      | ((span: AgentegritySpan) => T)
      | undefined;
    const span = new AgentegritySpan(name, this.adapter, this.catchAll);
    if (fn) {
      try {
        const out = fn(span);
        // Auto-end on synchronous return; callers may end manually for async
        return out;
      } finally {
        span.end();
      }
    }
    return undefined as unknown as T;
  }
}

/**
 * Build an `experimental_telemetry` object for the Vercel AI SDK.
 * Pass the returned object straight into `generateText`,
 * `streamText`, `generateObject`, etc.
 */
export function instrument(
  options: InstrumentOptions = {},
): { isEnabled: true; tracer: AgentegrityTracer } {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: "vercel_ai", profile: options.profile })
    : defaultAdapter();
  return {
    isEnabled: true,
    tracer: new AgentegrityTracer(ad, options.catchAll ?? false),
  };
}

export async function report(): Promise<SessionSummary> {
  if (_default === null) {
    return {
      adapter: "vercel_ai",
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

export function adapter(): DefaultAdapter {
  return defaultAdapter();
}

export { AgentegrityTracer, AgentegritySpan };
export type { SessionExporter };
