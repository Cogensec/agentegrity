/**
 * Evidence types — TypeScript mirror of Python's
 * `agentegrity.core.attestation.Evidence`. The `evidence_type` field
 * is the discriminator; new types are non-breaking additions to a
 * free-form string union, so TS readers must treat unknown values
 * as opaque rather than as type errors.
 */

export type EvidenceType =
  | "layer_result"
  | "topology"
  | "topology_change"
  | "peer_message"
  | "handoff"
  | "shared_memory_write"
  | "broadcast"
  | "decision"
  | "peer_attestation";

export interface Evidence {
  evidence_type: EvidenceType | string;
  source: string;
  content_hash: string;
  summary: string;
  timestamp?: string;
}
