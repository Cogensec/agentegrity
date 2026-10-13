/**
 * AgentegrityReporter — minimal HTTP client that emits session events
 * to any backend implementing the Agentegrity Exporter HTTP API
 * (see schemas/openapi.yaml).
 *
 * Shape-for-shape parity with Python `SessionExporter`: a single
 * `session_id` is generated client-side and threaded through
 * session_start, event, and session_end calls.
 *
 * Fail-open: any network or serialization error is caught, logged via
 * the `onError` callback (default console.warn), and swallowed — a
 * broken dashboard must never break the instrumented agent. This
 * matches the guarantee the Python `_BaseAdapter._notify_exporters`
 * gives on the server side.
 */

import type {
  AgentProfile,
  EventType,
  ExporterDescription,
  FrameworkEvent,
  IntegrityScore,
  SessionSummary,
} from "./types.js";

export interface ReporterOptions {
  /**
   * Base URL of the backend. The reporter appends `/sessions`,
   * `/sessions/{id}/events`, and `/sessions/{id}/end`. Must be HTTPS or a
   * loopback address; anything else, or no URL, sends nothing.
   */
  baseUrl?: string;
  /** Agent profile, forwarded verbatim in the session_start call. */
  profile: AgentProfile;
  /** Adapter name. Defaults to "typescript". */
  adapterName?: string;
  /** Optional Bearer token for `Authorization`. */
  apiKey?: string;
  /** Custom fetch implementation (default: global `fetch`). */
  fetchImpl?: typeof fetch;
  /** Called when a request fails. Defaults to `console.warn`. */
  onError?: (err: unknown, step: string) => void;
}

/**
 * Offline mode skips the network but keeps exporter fan-out. The test suite
 * uses it so CI runs don't pay ECONNREFUSED round-trips.
 */
function isOffline(): boolean {
  const env = (globalThis as unknown as { process?: { env?: Record<string, string | undefined> } })
    .process?.env;
  const off = env?.AGENTEGRITY_OFFLINE;
  return off === "1" || off === "true" || off === "TRUE";
}

/**
 * Whether session data and a Bearer token may be sent to `baseUrl`: HTTPS,
 * or a loopback host whose traffic never leaves the machine. Anything else,
 * including a URL that does not parse, is refused. The Python exporter
 * applies the same rule.
 */
function isTransportSafe(baseUrl: string): boolean {
  try {
    const u = new URL(baseUrl);
    if (u.protocol === "https:") return true;
    const host = u.hostname.toLowerCase();
    return (
      host === "localhost" ||
      host.endsWith(".localhost") ||
      host === "127.0.0.1" ||
      host === "::1" ||
      host === "[::1]"
    );
  } catch {
    return false;
  }
}

/** `url` without trailing slashes; a loop, since a `/\/+$/` regex is quadratic on input like "////x". */
function trimTrailingSlashes(url: string): string {
  let end = url.length;
  while (end > 0 && url[end - 1] === "/") end -= 1;
  return url.slice(0, end);
}

function randomHex32(): string {
  // Node 18+ and modern browsers both expose crypto.randomUUID.
  // Strip hyphens to match Python's uuid4().hex.
  const g = globalThis as unknown as { crypto?: { randomUUID?: () => string } };
  if (g.crypto?.randomUUID) return g.crypto.randomUUID().replace(/-/g, "");
  // Fallback: 128 random bits from Math.random (non-cryptographic but fine
  // for an id; no sensitive data is derived from it).
  let out = "";
  for (let i = 0; i < 32; i++) {
    out += Math.floor(Math.random() * 16).toString(16);
  }
  return out;
}

export class AgentegrityReporter {
  readonly sessionId: string;
  readonly adapterName: string;
  private readonly baseUrl?: string;
  private readonly profile: AgentProfile;
  private readonly apiKey?: string;
  private readonly fetchImpl: typeof fetch;
  private readonly onError: (err: unknown, step: string) => void;
  private started = false;
  private ended = false;

  constructor(opts: ReporterOptions) {
    this.sessionId = randomHex32();
    this.adapterName = opts.adapterName ?? "typescript";
    const baseUrl = opts.baseUrl === undefined ? undefined : trimTrailingSlashes(opts.baseUrl);
    this.profile = opts.profile;
    this.apiKey = opts.apiKey;
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.onError =
      opts.onError ??
      ((err, step) => {
        // eslint-disable-next-line no-console
        console.warn(`agentegrity reporter ${step} failed:`, err);
      });
    if (baseUrl !== undefined && !isTransportSafe(baseUrl)) {
      // Reported once; with no usable URL the reporter sends nothing.
      this.onError(
        new Error(
          `nothing will be sent: ${baseUrl} is neither HTTPS nor a loopback ` +
            `address. Session data and the token are only sent over HTTPS or ` +
            `to this machine.`,
        ),
        "config",
      );
    }
    this.baseUrl = baseUrl !== undefined && isTransportSafe(baseUrl) ? baseUrl : undefined;
  }

  /** Open the session. Idempotent — subsequent calls are no-ops. */
  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    await this.post("/sessions", "session_start", {
      session_id: this.sessionId,
      adapter_name: this.adapterName,
      profile: this.profile,
    });
  }

  /**
   * Emit a FrameworkEvent. Wraps the event payload with `session_id`
   * per the JSON Schema. Auto-starts the session on first call.
   */
  async emit(event: {
    event_type: EventType;
    data?: Record<string, unknown>;
    evaluation_result?: IntegrityScore | null;
    timestamp?: string;
  }): Promise<void> {
    if (!this.started) await this.start();
    const full: FrameworkEvent = {
      event_type: event.event_type,
      timestamp: event.timestamp ?? new Date().toISOString(),
      adapter_name: this.adapterName,
      data: event.data ?? {},
      evaluation_result: event.evaluation_result ?? null,
    };
    await this.post(
      `/sessions/${this.sessionId}/events`,
      "event",
      { session_id: this.sessionId, event: full },
    );
  }

  /** Close the session. Idempotent — subsequent calls are no-ops. */
  async end(summary: Partial<SessionSummary> = {}): Promise<void> {
    if (this.ended) return;
    this.ended = true;
    const full: SessionSummary = {
      adapter: this.adapterName,
      agent_id: this.profile.agent_id,
      evaluations: 0,
      events: 0,
      attestation_records: 0,
      chain_hash_linked: null,
      chain_signatures: "none",
      enforce_mode: false,
      ...summary,
    };
    await this.post(
      `/sessions/${this.sessionId}/end`,
      "session_end",
      { session_id: this.sessionId, summary: full },
    );
  }

  /**
   * The HTTP destination for the session summary, by origin only, or `null`
   * when nothing is sent: no URL, an unsafe URL, or offline mode.
   */
  describe(): ExporterDescription | null {
    if (this.baseUrl === undefined || isOffline()) return null;
    return { type: "AgentegrityReporter", target: new URL(this.baseUrl).origin };
  }

  private async post(
    path: string,
    step: string,
    body: unknown,
  ): Promise<void> {
    if (this.baseUrl === undefined || isOffline()) return;
    try {
      const headers: Record<string, string> = {
        "content-type": "application/json",
      };
      if (this.apiKey) {
        headers.authorization = `Bearer ${this.apiKey}`;
      }
      // A redirect fails the request instead of carrying the token to
      // wherever the Location header points.
      const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        redirect: "error",
      });
      if (!res.ok) {
        this.onError(
          new Error(`HTTP ${res.status} on ${path}`),
          step,
        );
      }
    } catch (err) {
      this.onError(err, step);
    }
  }
}
