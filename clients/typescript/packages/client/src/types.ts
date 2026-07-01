/**
 * TypeScript types mirroring the Agentegrity JSON Schemas at
 * schemas/exporter/*.json. These are the canonical wire shapes for
 * the SessionExporter protocol and the `@agentegrity/client` HTTP API.
 */

export type EventType =
  | "pre_tool_use"
  | "post_tool_use"
  | "post_tool_use_failure"
  | "user_prompt_submit"
  | "stop"
  | "subagent_start"
  | "subagent_stop"
  | "pre_compact"
  // v0.8: multi-agent canonical events
  | "topology_declared"
  | "topology_change"
  | "peer_message"
  | "shared_memory_write"
  | "broadcast"
  | "task_started"
  // emitted by `_BaseAdapter._handle_subagent_stop` when a stop fires
  // without a matching start (T-ORPHAN-LIFECYCLE mitigation).
  | "subagent_orphan";

export interface AgentProfile {
  agent_id: string;
  name: string;
  agent_type: string;
  capabilities: string[];
  deployment_context: string;
  risk_tier: string;
  framework?: string | null;
  model_provider?: string | null;
  model_id?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
  [extra: string]: unknown;
}

export interface LayerResult {
  layer_name: string;
  score: number;
  passed: boolean;
  action: string;
  details?: Record<string, unknown>;
  latency_ms?: number;
}

export interface IntegrityScore {
  composite: number;
  properties: Record<string, number>;
  layer_results: LayerResult[];
  action?: string;
  [extra: string]: unknown;
}

export interface FrameworkEvent {
  event_type: EventType;
  timestamp: string;
  adapter_name: string;
  data: Record<string, unknown>;
  evaluation_result?: IntegrityScore | null;
}

export interface SessionSummary {
  adapter: string;
  agent_id: string | null;
  evaluations: number;
  events: number;
  attestation_records: number;
  chain_hash_linked: boolean;
  enforce_mode: boolean;
  [extra: string]: unknown;
}

export interface SessionStartPayload {
  session_id: string;
  adapter_name: string;
  profile: AgentProfile;
}

export interface EventPayload {
  session_id: string;
  event: FrameworkEvent;
}

export interface SessionEndPayload {
  session_id: string;
  summary: SessionSummary;
}

/**
 * SessionExporter — TypeScript mirror of the Python `SessionExporter`
 * Protocol. Implement any subset of the three hooks; missing hooks are
 * skipped. Errors thrown from any hook are caught and logged — a broken
 * exporter must never break the instrumented agent.
 */
export interface SessionExporter {
  on_session_start?(
    session_id: string,
    adapter_name: string,
    profile: AgentProfile,
  ): void | Promise<void>;
  on_event?(session_id: string, event: FrameworkEvent): void | Promise<void>;
  on_session_end?(
    session_id: string,
    summary: SessionSummary,
  ): void | Promise<void>;
}
