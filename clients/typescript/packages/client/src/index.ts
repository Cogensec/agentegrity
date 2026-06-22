/**
 * `@agentegrity/client` — TypeScript client for the Agentegrity
 * Exporter HTTP API. Emit agent events from a Node / Bun / browser
 * agent to any backend implementing the contract in
 * `schemas/openapi.yaml` (including the commercial `agentegrity-pro`
 * dashboard).
 */

export { AgentegrityReporter } from "./reporter.js";
export type { ReporterOptions } from "./reporter.js";
export {
  createDefaultAdapter,
} from "./default.js";
export type {
  AdapterConfig,
  DefaultAdapter,
  EmittableEvent,
} from "./default.js";
export type {
  AgentProfile,
  EventPayload,
  EventType,
  FrameworkEvent,
  IntegrityScore,
  LayerResult,
  SessionEndPayload,
  SessionExporter,
  SessionStartPayload,
  SessionSummary,
} from "./types.js";
// v0.8: multi-agent
export {
  AgentMember,
  AgentRole,
  AgentTopology,
  TopologyChange,
  TopologyKind,
} from "./topology.js";
export type {
  AgentMemberInit,
  AgentTopologyInit,
  TopologyChangeData,
} from "./topology.js";
export type { Evidence, EvidenceType } from "./evidence.js";
