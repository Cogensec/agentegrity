/**
 * AgentegrityLangChainHandler — LangChain JS callback handler that
 * forwards lifecycle events to an agentegrity DefaultAdapter.
 *
 * The handler structurally conforms to LangChain's `BaseCallbackHandler`
 * without importing from `@langchain/core`, so the package builds without
 * it. LangChain duck-types handlers by method name.
 *
 * Event mapping:
 *   handleChainStart (top level)  → user_prompt_submit
 *   handleChainEnd   (top level)  → stop
 *   handleChainError (top level)  → stop (with the error)
 *   handleToolStart               → pre_tool_use
 *   handleToolEnd                 → post_tool_use
 *   handleToolError               → post_tool_use_failure
 *   handleLLMEnd                  → token usage (usage_metadata, per run id)
 *
 * A chain is top level when LangChain gives it no parent run id; nested
 * chains, graph nodes and model calls stay inside their parent's events.
 */

import type { DefaultAdapter, TokenUsage } from "@agentegrity/client";

interface SerializedLike {
  id?: string[];
  name?: string;
  [k: string]: unknown;
}

type Details = Record<string, unknown>;

interface UsageMetadataLike {
  input_tokens?: number;
  output_tokens?: number;
  input_token_details?: Details;
  output_token_details?: Details;
}

interface LLMResultLike {
  generations?: Array<Array<{ message?: { usage_metadata?: UsageMetadataLike; response_metadata?: Details } }>>;
  llmOutput?: { tokenUsage?: { promptTokens?: number; completionTokens?: number }; model_name?: unknown } | null;
}

export class AgentegrityLangChainHandler {
  readonly name = "agentegrity";
  /** LangChain awaits our async hooks, so events land in order. */
  readonly awaitHandlers = true;

  private readonly adapter: DefaultAdapter;
  private readonly toolNames = new Map<string, string>();
  private readonly startModels = new Map<string, string>();

  constructor(adapter: DefaultAdapter) {
    this.adapter = adapter;
  }

  /** Public shutdown helper — fires session_end. */
  async close(): Promise<void> {
    await this.adapter.end();
  }

  // ─── LangChain BaseCallbackHandler surface ────────────────────────────────

  /**
   * The callback manager calls this as (chain, inputs, runId, parentRunId,
   * tags, metadata, runType, runName), from 0.3 to 1.x, although
   * BaseCallbackHandler declares parentRunId as the eighth parameter.
   */
  async handleChainStart(
    _chain: SerializedLike,
    inputs: Record<string, unknown>,
    _runId?: string,
    parentRunId?: string,
  ): Promise<void> {
    if (parentRunId) return;
    await this.adapter.emit({ event_type: "user_prompt_submit", data: { inputs } });
  }

  async handleChainEnd(outputs: Record<string, unknown>, _runId?: string, parentRunId?: string): Promise<void> {
    if (parentRunId) return;
    await this.adapter.emit({ event_type: "stop", data: { outputs } });
  }

  async handleChainError(err: unknown, _runId?: string, parentRunId?: string): Promise<void> {
    if (parentRunId) return;
    await this.adapter.emit({ event_type: "stop", data: { error: errorMessage(err) } });
  }

  /** Called as (tool, input, runId, parentRunId, tags, metadata, runName); runName is the tool's name. */
  async handleToolStart(
    tool: SerializedLike,
    input: string,
    runId?: string,
    _parentRunId?: string,
    _tags?: string[],
    _metadata?: Record<string, unknown>,
    runName?: string,
  ): Promise<void> {
    const tool_name = stringOr(runName) ?? toolName(tool);
    if (runId) this.toolNames.set(runId, tool_name);
    await this.adapter.emit({ event_type: "pre_tool_use", data: { tool_name, tool_input: input } });
  }

  async handleToolEnd(output: unknown, runId?: string): Promise<void> {
    await this.adapter.emit({
      event_type: "post_tool_use",
      data: { tool_name: this.takeToolName(runId), tool_response: toolOutput(output) },
    });
  }

  async handleToolError(err: unknown, runId?: string): Promise<void> {
    await this.adapter.emit({
      event_type: "post_tool_use_failure",
      data: { tool_name: this.takeToolName(runId), error: errorMessage(err) },
    });
  }

  async handleChatModelStart(
    _llm: SerializedLike,
    _messages: unknown,
    runId: string,
    _parentRunId?: string,
    _extraParams?: Record<string, unknown>,
    _tags?: string[],
    metadata?: Record<string, unknown>,
  ): Promise<void> {
    const model = metadata?.ls_model_name;
    if (typeof model === "string" && model) this.startModels.set(runId, model);
  }

  /**
   * Record one model call. Its usage is read once: providers that return
   * several choices repeat the whole call's usage on each. Keyed by run id,
   * so a handler attached twice counts the call once.
   */
  async handleLLMEnd(output: LLMResultLike, runId: string): Promise<void> {
    const startModel = this.startModels.get(runId) ?? null;
    this.startModels.delete(runId);
    const message = output?.generations?.flat().find((g) => g?.message?.usage_metadata)?.message;
    if (message?.usage_metadata) {
      const meta = message.response_metadata ?? {};
      const model = stringOr(meta.model_name) ?? stringOr(meta.model) ?? startModel;
      this.adapter.recordUsage(`langchain:${runId}`, model, metadataUsage(message.usage_metadata), {
        source: "provider_response",
      });
      return;
    }
    const legacy = output?.llmOutput?.tokenUsage;
    if (legacy) {
      this.adapter.recordUsage(`langchain:${runId}`, stringOr(output.llmOutput?.model_name) ?? startModel, {
        input_tokens: legacy.promptTokens ?? 0,
        output_tokens: legacy.completionTokens ?? 0,
      }, { source: "provider_response" });
    }
  }

  private takeToolName(runId: string | undefined): string {
    if (!runId) return "unknown";
    const name = this.toolNames.get(runId) ?? "unknown";
    this.toolNames.delete(runId);
    return name;
  }
}

/**
 * Normalize `usage_metadata`, whose input count already includes the cache.
 * Provider packages add keys beyond the base schema: Anthropic can split
 * cache writes into `ephemeral_*` keys, OpenAI prefixes keys by service tier
 * (`priority_cache_read`, `flex_reasoning`).
 */
function metadataUsage(usage: UsageMetadataLike): TokenUsage {
  const sum = (details: Details | undefined, match: (key: string) => boolean) =>
    Object.entries(details ?? {}).reduce((total, [k, v]) => total + (match(k) && typeof v === "number" ? v : 0), 0);
  const inputs = usage.input_token_details;
  const outputs = usage.output_token_details;
  return {
    input_tokens: usage.input_tokens ?? 0,
    output_tokens: usage.output_tokens ?? 0,
    cache_read_tokens: inputs ? sum(inputs, (k) => k.endsWith("cache_read")) : null,
    cache_write_tokens: inputs
      ? Math.max(sum(inputs, (k) => k.endsWith("cache_creation")), sum(inputs, (k) => k.startsWith("ephemeral_")))
      : null,
    reasoning_tokens: outputs ? sum(outputs, (k) => k.endsWith("reasoning")) : null,
  };
}

function stringOr(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** Recent LangChain passes the ToolMessage; report its content. */
function toolOutput(output: unknown): unknown {
  const content = (output as { content?: unknown } | null)?.content;
  return content !== undefined && typeof output === "object" ? content : output;
}

function toolName(tool: SerializedLike | undefined): string {
  if (!tool) return "unknown";
  if (tool.name && typeof tool.name === "string") return tool.name;
  if (Array.isArray(tool.id) && tool.id.length > 0) {
    const last = tool.id[tool.id.length - 1];
    if (typeof last === "string") return last;
  }
  return "unknown";
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  try {
    return typeof err === "string" ? err : JSON.stringify(err);
  } catch {
    return String(err);
  }
}
