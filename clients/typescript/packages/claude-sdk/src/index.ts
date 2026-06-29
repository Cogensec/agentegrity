/**
 * `@agentegrity/claude-sdk` — zero-config adapter for the Claude Agent
 * SDK (`@anthropic-ai/claude-agent-sdk`). Mirrors the Python
 * `agentegrity.claude` module 1:1.
 *
 * Usage:
 *
 * ```ts
 * import { ClaudeSDKClient } from "@anthropic-ai/claude-agent-sdk";
 * import { hooks, report } from "@agentegrity/claude-sdk";
 *
 * const client = new ClaudeSDKClient({ hooks: hooks() });
 * // ... run agent ...
 * console.log(await report());
 * ```
 */

import {
  createDefaultAdapter,
  type AdapterConfig,
  type DefaultAdapter,
  type SessionExporter,
  type SessionSummary,
  type AgentProfile,
} from "@agentegrity/client";

let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) {
    _default = createDefaultAdapter({ adapterName: "claude" });
  }
  return _default;
}

export interface HooksOptions {
  profile?: Partial<AgentProfile>;
  /** Measure-only vs. enforcing hooks. v0.5.0 accepts this flag but ignores it (parity with TS 0.4.0). */
  enforce?: boolean;
}

/**
 * The Claude Agent SDK hooks object. Maps the SDK's hook event names
 * to callbacks that forward to the agentegrity reporter. Mirrors
 * Python's `ClaudeAdapter.create_hooks()` output shape.
 *
 * Each hook callback returns `undefined` (measure-only). If
 * `options.enforce` is set, a future release will return
 * `{ continue: false }` on block actions — for v0.5.0 all hooks are
 * pass-through.
 */
export function hooks(options: HooksOptions = {}): Record<string, unknown> {
  if (options.profile) {
    // Non-default profile → build a fresh adapter, leave module global alone
    const one = createDefaultAdapter({ adapterName: "claude", profile: options.profile });
    return buildHookObject(one);
  }
  return buildHookObject(defaultAdapter());
}

function buildHookObject(ad: DefaultAdapter): Record<string, unknown> {
  const emit = (event_type: string, data: Record<string, unknown>) =>
    ad.emit({
      event_type: event_type as Parameters<DefaultAdapter["emit"]>[0]["event_type"],
      data,
    });

  // Claude Agent SDK 0.1.x accepts a map of hook-name → async callbacks.
  // The shape mirrors the Python ClaudeAdapter.create_hooks() output.
  return {
    user_prompt_submit: async (input: unknown) => {
      await emit("user_prompt_submit", { input });
    },
    pre_tool_use: async (tool: unknown) => {
      const t = (tool ?? {}) as { tool_name?: string; tool_input?: unknown };
      await emit("pre_tool_use", {
        tool_name: t.tool_name ?? "unknown",
        tool_input: t.tool_input,
      });
    },
    post_tool_use: async (tool: unknown) => {
      const t = (tool ?? {}) as { tool_name?: string; tool_response?: unknown };
      await emit("post_tool_use", {
        tool_name: t.tool_name ?? "unknown",
        tool_response: t.tool_response,
      });
    },
    stop: async (output: unknown) => {
      await emit("stop", { output });
    },
    subagent_start: async (info: unknown) => {
      await emit("subagent_start", { info });
    },
    subagent_stop: async (info: unknown) => {
      await emit("subagent_stop", { info });
    },
    pre_compact: async (info: unknown) => {
      await emit("pre_compact", { info });
    },
  };
}

/** Synchronous snapshot of the current session summary. */
export async function report(): Promise<SessionSummary> {
  if (_default === null) {
    return {
      adapter: "claude",
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
