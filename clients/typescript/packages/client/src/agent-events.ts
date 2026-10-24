/**
 * Opt-in metadata-only cross-agent contract, independent of legacy exporter
 * FrameworkEvent. This module does not verify source identity or effects.
 */

export const AGENT_EVENT_SCHEMA_VERSION = "1.0" as const;

export type SourceAssurance = "unverified" | "host_asserted" | "authenticated";
export type Capability =
  | "observe"
  | "evaluate"
  | "authorize"
  | "verify_effect"
  | "recover";
export type CapabilityStatus = "unavailable" | "implemented" | "validated";

export interface EventSourceV1 {
  host: string;
  integration: string;
  assurance: SourceAssurance;
  evidence_ref?: string;
}

export interface CanonicalAgentEventV1 {
  schema_version: typeof AGENT_EVENT_SCHEMA_VERSION;
  event_id: string;
  event_type: string;
  occurred_at: string;
  agent_id: string;
  run_id: string;
  source: EventSourceV1;
  session_id?: string;
  action_id?: string;
  parent_event_id?: string;
  tool_name?: string;
  effect?: string;
  outcome?: string;
}

export interface CapabilityEvidenceV1 {
  capability: Capability;
  status: CapabilityStatus;
  evidence_ref?: string;
}

export interface AgentCapabilityCardV1 {
  integration: string;
  host: string;
  host_version: string;
  claims: CapabilityEvidenceV1[];
}

const EVENT_FIELDS = new Set([
  "schema_version", "event_id", "event_type", "occurred_at", "agent_id",
  "run_id", "source", "session_id", "action_id", "parent_event_id",
  "tool_name", "effect", "outcome",
]);
const SOURCE_FIELDS = new Set(["host", "integration", "assurance", "evidence_ref"]);
const CLAIM_FIELDS = new Set(["capability", "status", "evidence_ref"]);
const CARD_FIELDS = new Set(["integration", "host", "host_version", "claims"]);
const CAPABILITIES: Capability[] = [
  "observe", "evaluate", "authorize", "verify_effect", "recover",
];
const STATUSES: CapabilityStatus[] = ["unavailable", "implemented", "validated"];
const ASSURANCES: SourceAssurance[] = ["unverified", "host_asserted", "authenticated"];
const EVENT_TYPE = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;
// RFC 3339 subset agreed with the Python parser and JSON schema: exact
// calendar date, uppercase T/Z, second precision and <=6 fractional digits.
const WIRE_TIMESTAMP = /^([0-9]{4})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$/;

function validWireTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = WIRE_TIMESTAMP.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= daysInMonth[month - 1]!;
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function allowedFields(value: Record<string, unknown>, allowed: Set<string>): void {
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new TypeError("unknown contract field");
  }
}

function identifier(value: unknown, label: string): string {
  if (
    typeof value !== "string" || Array.from(value).length > 256 ||
    value.trim().length === 0
  ) {
    throw new TypeError(`${label} must be a nonempty string up to 256 characters`);
  }
  return value;
}

function optionalIdentifier(
  value: Record<string, unknown>, name: string,
): void {
  if (Object.prototype.hasOwnProperty.call(value, name)) {
    identifier(value[name], name);
  }
}

/**
 * Strictly validate a canonical event before crossing an integration boundary.
 * An "authenticated" field remains a caller claim; validate real provenance
 * separately in the receiver. No arbitrary prompt or tool payloads permitted.
 */
export function parseCanonicalAgentEvent(value: unknown): CanonicalAgentEventV1 {
  const raw = object(value, "canonical event");
  allowedFields(raw, EVENT_FIELDS);
  if (raw.schema_version !== AGENT_EVENT_SCHEMA_VERSION) {
    throw new TypeError("unsupported canonical event schema version");
  }
  for (const key of ["event_id", "agent_id", "run_id"]) identifier(raw[key], key);
  if (typeof raw.event_type !== "string" || !EVENT_TYPE.test(raw.event_type)) {
    throw new TypeError("invalid canonical event type");
  }
  if (!validWireTimestamp(raw.occurred_at)) {
    throw new TypeError("occurred_at must be a valid timezone-aware ISO timestamp");
  }
  const source = object(raw.source, "event source");
  allowedFields(source, SOURCE_FIELDS);
  identifier(source.host, "host");
  identifier(source.integration, "integration");
  if (!ASSURANCES.includes(source.assurance as SourceAssurance)) {
    throw new TypeError("invalid source assurance");
  }
  optionalIdentifier(source, "evidence_ref");
  if (source.assurance === "authenticated" && source.evidence_ref === undefined) {
    throw new TypeError("authenticated source requires evidence_ref");
  }
  for (const key of [
    "session_id", "action_id", "parent_event_id", "tool_name",
    "effect", "outcome",
  ]) optionalIdentifier(raw, key);
  return raw as unknown as CanonicalAgentEventV1;
}

/** Validate every level explicitly, including unavailable capabilities. */
export function parseAgentCapabilityCard(value: unknown): AgentCapabilityCardV1 {
  const raw = object(value, "capability card");
  allowedFields(raw, CARD_FIELDS);
  for (const key of ["integration", "host", "host_version"]) identifier(raw[key], key);
  if (!Array.isArray(raw.claims) || raw.claims.length !== CAPABILITIES.length) {
    throw new TypeError("declare all five capabilities");
  }
  const seen = new Set<Capability>();
  for (const item of raw.claims as unknown[]) {
    const claim = object(item, "capability claim");
    allowedFields(claim, CLAIM_FIELDS);
    const capability = claim.capability as Capability;
    if (!CAPABILITIES.includes(capability) || seen.has(capability)) {
      throw new TypeError("invalid or duplicate capability");
    }
    seen.add(capability);
    if (!STATUSES.includes(claim.status as CapabilityStatus)) {
      throw new TypeError("invalid capability status");
    }
    optionalIdentifier(claim, "evidence_ref");
    if (claim.status === "validated" && claim.evidence_ref === undefined) {
      throw new TypeError("validated capability requires evidence_ref");
    }
    if (claim.status === "unavailable" && claim.evidence_ref !== undefined) {
      throw new TypeError("unavailable capability cannot claim evidence");
    }
  }
  return raw as unknown as AgentCapabilityCardV1;
}
