/**
 * createDefaultAdapter — shared lazy singleton + lifecycle + exporter
 * fan-out used by every `@agentegrity/<framework>` adapter package.
 *
 * Mirrors the Python `_BaseAdapter` in three respects:
 *   1. one session_id per adapter instance (generated lazily on first event)
 *   2. fail-open fan-out to zero or more SessionExporter subscribers in
 *      addition to the primary HTTP reporter
 *   3. idempotent start/end with a process-lifecycle shutdown hook
 *      (process.beforeExit) so session_end fires on clean exit
 *
 * Each adapter package calls `createDefaultAdapter({ adapterName: "langchain" })`
 * once at module load, wires its framework-native hook to `emit`, and
 * re-exports the returned `report`, `reset`, `registerExporter`, `adapter`
 * functions. All framework glue is <100 lines on top of this.
 */

import { AgentegrityReporter, type ReporterOptions } from "./reporter.js";
import {
  AgentRole,
  AgentTopology,
  TopologyChange,
} from "./topology.js";
import type {
  AgentProfile,
  EventType,
  FrameworkEvent,
  IntegrityScore,
  SessionExporter,
  SessionSummary,
} from "./types.js";

export interface AdapterConfig {
  /** Adapter name (e.g. "langchain"). Sent as `adapter_name` on the wire. */
  adapterName: string;
  /**
   * Partial profile. Merged with framework defaults to produce the
   * session_start `profile` object.
   */
  profile?: Partial<AgentProfile>;
  /** Options passed straight to the underlying AgentegrityReporter. */
  reporterOptions?: Partial<ReporterOptions>;
  /**
   * Default base URL if `reporterOptions.baseUrl` and
   * `AGENTEGRITY_URL` env var are both unset. Defaults to `http://localhost:8787`.
   */
  defaultBaseUrl?: string;
}

export interface EmittableEvent {
  event_type: EventType;
  data?: Record<string, unknown>;
  evaluation_result?: IntegrityScore | null;
  timestamp?: string;
}

export interface DefaultAdapter {
  /** The wrapped reporter — escape hatch for advanced users. */
  readonly reporter: AgentegrityReporter;
  /**
   * The adapter name passed to {@link createDefaultAdapter}. Mirrors the
   * Python `_BaseAdapter.name` field — used by the cross-package
   * conformance suite to verify each `@agentegrity/<framework>` package
   * is wired to the correct seam.
   */
  readonly adapterName: string;
  /** Current session id (stable for the lifetime of this adapter). */
  readonly sessionId: string;
  /** True if the adapter is disabled via AGENTEGRITY_DISABLED=1. */
  readonly disabled: boolean;
  /** Running count of emitted events (for the session summary). */
  readonly eventCount: number;
  /**
   * Currently-declared multi-agent topology (v0.8). `null` until
   * {@link setTopology} is called by a team-aware adapter. Single-agent
   * adapters (claude-sdk, vercel-ai) never set this.
   */
  readonly topology: AgentTopology | null;
  /** Idempotent. Safe to call from framework hooks. */
  ensureStart(): Promise<void>;
  /** Emit a FrameworkEvent. Auto-starts. Fans out to registered exporters. */
  emit(event: EmittableEvent): Promise<void>;
  /** Fire session_end with an optional summary override. Idempotent. */
  end(summary?: Partial<SessionSummary>): Promise<void>;
  /** Discard session state; next emit starts a fresh session. */
  reset(): void;
  /** Register an additional SessionExporter subscriber. */
  registerExporter(exporter: SessionExporter): void;
  /** Snapshot summary for `report()` helpers. */
  getSummary(): SessionSummary;
  /**
   * Declare or update the in-process multi-agent topology this
   * adapter participates in (v0.8).
   *
   * First call emits a `topology_declared` event. Subsequent calls
   * with a structurally-distinct topology (different `contentHash`)
   * emit `topology_change` with a {@link TopologyChange} diff in
   * `data.change`. Structurally-identical re-sets are a no-op.
   *
   * Mirrors Python `_BaseAdapter.set_topology(topology, my_role)`.
   */
  setTopology(topology: AgentTopology, myRole?: AgentRole): Promise<void>;
}

function readEnv(name: string): string | undefined {
  const env = (globalThis as unknown as { process?: { env?: Record<string, string | undefined> } })
    .process?.env;
  return env?.[name];
}

function isDisabled(): boolean {
  const v = readEnv("AGENTEGRITY_DISABLED");
  return v === "1" || v === "true" || v === "TRUE";
}

function defaultProfile(adapterName: string, override?: Partial<AgentProfile>): AgentProfile {
  return {
    agent_id: override?.agent_id ?? `${adapterName}-agent`,
    name: override?.name ?? adapterName,
    agent_type: override?.agent_type ?? "unknown",
    capabilities: override?.capabilities ?? [],
    deployment_context: override?.deployment_context ?? "development",
    risk_tier: override?.risk_tier ?? "medium",
    framework: override?.framework ?? adapterName,
    model_provider: override?.model_provider ?? null,
    model_id: override?.model_id ?? null,
    metadata: override?.metadata ?? {},
    ...override,
  };
}

/**
 * Invoke a SessionExporter method, catching and logging errors so a
 * broken subscriber can never break the agent (fail-open guarantee).
 */
async function safeCall(
  fn: (() => void | Promise<void>) | undefined,
  label: string,
  onError: (err: unknown, step: string) => void,
): Promise<void> {
  if (!fn) return;
  try {
    await fn();
  } catch (err) {
    onError(err, label);
  }
}

export function createDefaultAdapter(config: AdapterConfig): DefaultAdapter {
  const adapterName = config.adapterName;
  const disabled = isDisabled();
  const baseUrl =
    config.reporterOptions?.baseUrl ??
    readEnv("AGENTEGRITY_URL") ??
    config.defaultBaseUrl ??
    "http://localhost:8787";
  const apiKey = config.reporterOptions?.apiKey ?? readEnv("AGENTEGRITY_TOKEN");
  const profile = defaultProfile(adapterName, config.profile);

  const onError =
    config.reporterOptions?.onError ??
    ((err: unknown, step: string) => {
      // eslint-disable-next-line no-console
      console.warn(`[agentegrity:${adapterName}] ${step}:`, err);
    });

  const reporter = new AgentegrityReporter({
    baseUrl,
    profile,
    adapterName,
    apiKey,
    fetchImpl: config.reporterOptions?.fetchImpl,
    onError,
  });

  const exporters: SessionExporter[] = [];
  let eventCount = 0;
  let evaluationCount = 0;
  let started = false;
  let ended = false;
  let shutdownRegistered = false;
  let topology: AgentTopology | null = null;
  let myRole: AgentRole | null = null;

  const registerShutdown = () => {
    if (shutdownRegistered || disabled) return;
    const proc = (globalThis as unknown as { process?: NodeJS.Process }).process;
    if (!proc?.once) return;
    shutdownRegistered = true;
    proc.once("beforeExit", () => {
      void api.end();
    });
  };

  const api: DefaultAdapter = {
    reporter,
    adapterName,
    get sessionId() {
      return reporter.sessionId;
    },
    disabled,
    get eventCount() {
      return eventCount;
    },

    async ensureStart() {
      if (disabled || started) return;
      started = true;
      registerShutdown();
      await reporter.start();
      for (const exp of exporters) {
        await safeCall(
          () => exp.on_session_start?.(reporter.sessionId, adapterName, profile),
          "exporter.on_session_start",
          onError,
        );
      }
    },

    async emit(event) {
      if (disabled) return;
      if (!started) await api.ensureStart();
      eventCount++;
      if (event.evaluation_result) evaluationCount++;
      const full: FrameworkEvent = {
        event_type: event.event_type,
        timestamp: event.timestamp ?? new Date().toISOString(),
        adapter_name: adapterName,
        data: event.data ?? {},
        evaluation_result: event.evaluation_result ?? null,
      };
      await reporter.emit(event);
      for (const exp of exporters) {
        await safeCall(
          () => exp.on_event?.(reporter.sessionId, full),
          "exporter.on_event",
          onError,
        );
      }
    },

    async end(summary) {
      if (disabled || ended || !started) {
        ended = true;
        return;
      }
      ended = true;
      const full = api.getSummary();
      if (summary) Object.assign(full, summary);
      await reporter.end(full);
      for (const exp of exporters) {
        await safeCall(
          () => exp.on_session_end?.(reporter.sessionId, full),
          "exporter.on_session_end",
          onError,
        );
      }
    },

    reset() {
      started = false;
      ended = false;
      eventCount = 0;
      evaluationCount = 0;
      topology = null;
      myRole = null;
    },

    get topology() {
      return topology;
    },

    async setTopology(next: AgentTopology, role?: AgentRole): Promise<void> {
      if (disabled) {
        topology = next;
        myRole = role ?? null;
        return;
      }
      const previous = topology;
      topology = next;
      myRole = role ?? null;
      if (previous === null) {
        await api.emit({
          event_type: "topology_declared",
          data: { topology: next.toDict(), my_role: myRole },
        });
        return;
      }
      if (previous.contentHash() === next.contentHash()) {
        return; // structurally identical; no-op
      }
      const change = TopologyChange.between(previous, next);
      await api.emit({
        event_type: "topology_change",
        data: {
          change: change.toDict(),
          topology: next.toDict(),
          my_role: myRole,
        },
      });
    },

    registerExporter(exporter) {
      // Idempotent on identity — re-registering the same instance must
      // not double-fire on_session_start / on_event / on_session_end.
      // Mirrors the Python `_BaseAdapter.register_exporter` contract.
      if (!exporters.includes(exporter)) {
        exporters.push(exporter);
      }
    },

    getSummary(): SessionSummary {
      return {
        adapter: adapterName,
        agent_id: profile.agent_id,
        evaluations: evaluationCount,
        events: eventCount,
        attestation_records: 0,
        chain_hash_linked: true,
        enforce_mode: false,
      };
    },
  };

  return api;
}
