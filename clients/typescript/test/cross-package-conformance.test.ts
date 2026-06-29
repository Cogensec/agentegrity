/**
 * Cross-package conformance test for every shipped `@agentegrity/<framework>`
 * adapter package.
 *
 * The TypeScript mirror of `tests/test_adapter_conformance.py`. Each
 * adapter package wraps the same `createDefaultAdapter` core in
 * `@agentegrity/client` with a different framework-native entry point
 * (Claude SDK hooks, LangChain callbacks, OpenAI agents run hooks,
 * CrewAI event bridge, Google ADK instrumenter, Vercel AI tracer).
 * The shared `adapter()` export is the framework-agnostic seam.
 *
 * Same canonical event stream is driven through each package's
 * `adapter()` and the same invariants are pinned per package:
 *
 *   1. `adapter()` returns a DefaultAdapter with the documented
 *      `adapterName` and `sessionId` is a stable hex string.
 *   2. After emitting events the eventCount matches.
 *   3. `report()` returns a SessionSummary with the expected shape.
 *   4. `registerExporter()` is idempotent — re-registering the same
 *      instance does not double-fire on_session_start.
 *   5. A registered exporter receives on_session_start once + on_event
 *      per emit; on_session_end fires after the adapter ends.
 *   6. A broken exporter (raises in every callback) does not crash
 *      `emit()`. Fail-open is preserved.
 *   7. After `reset()` the next emit starts a fresh session_id.
 *
 * Adding a new adapter: append one entry to ADAPTER_MODULES below.
 * The matrix runs against it automatically.
 */

import { afterEach, describe, expect, test } from "bun:test";
import type {
  AgentProfile,
  FrameworkEvent,
  SessionExporter,
  SessionSummary,
} from "@agentegrity/client";

// Each package re-exports the same five surface functions from its
// underlying createDefaultAdapter call. `adapter()` is the parity seam.
import * as claudeSdk from "@agentegrity/claude-sdk";
import * as langchain from "@agentegrity/langchain";
import * as openaiAgents from "@agentegrity/openai-agents";
import * as crewai from "@agentegrity/crewai";
import * as googleAdk from "@agentegrity/google-adk";
import * as vercelAi from "@agentegrity/vercel-ai";

interface AdapterModule {
  adapter: () => {
    readonly adapterName: string;
    readonly sessionId: string;
    readonly eventCount: number;
    emit: (event: {
      event_type: FrameworkEvent["event_type"];
      data?: Record<string, unknown>;
    }) => Promise<void>;
    end: () => Promise<void>;
    reset: () => void;
    registerExporter: (exporter: SessionExporter) => void;
  };
  reset: () => void;
  registerExporter: (exporter: SessionExporter) => void;
  report: () => Promise<SessionSummary>;
}

// Every shipped adapter package + the documented adapterName the
// shared client.createDefaultAdapter is called with. Mirrors
// ADAPTER_CLASSES in tests/test_adapter_conformance.py.
const ADAPTER_MODULES: ReadonlyArray<{
  expectedName: string;
  module: AdapterModule;
}> = [
  { expectedName: "claude", module: claudeSdk as unknown as AdapterModule },
  { expectedName: "langchain", module: langchain as unknown as AdapterModule },
  {
    expectedName: "openai_agents",
    module: openaiAgents as unknown as AdapterModule,
  },
  { expectedName: "crewai", module: crewai as unknown as AdapterModule },
  {
    expectedName: "google_adk",
    module: googleAdk as unknown as AdapterModule,
  },
  {
    expectedName: "vercel_ai",
    module: vercelAi as unknown as AdapterModule,
  },
];

// Disable the HTTP reporter for the entire test file. The DefaultAdapter
// fan-out path uses fetch() against an external base URL; in tests we
// only care about exporter callbacks and the in-process state.
process.env.AGENTEGRITY_OFFLINE = "1";

interface CapturedCall {
  kind: "session_start" | "event" | "session_end";
  sessionId: string;
  payload?: unknown;
}

class CapturingExporter implements SessionExporter {
  readonly calls: CapturedCall[] = [];
  async on_session_start(
    session_id: string,
    _adapter_name: string,
    _profile: AgentProfile,
  ): Promise<void> {
    this.calls.push({ kind: "session_start", sessionId: session_id });
  }
  async on_event(session_id: string, event: FrameworkEvent): Promise<void> {
    this.calls.push({
      kind: "event",
      sessionId: session_id,
      payload: event.event_type,
    });
  }
  async on_session_end(
    session_id: string,
    _summary: SessionSummary,
  ): Promise<void> {
    this.calls.push({ kind: "session_end", sessionId: session_id });
  }
}

class BrokenExporter implements SessionExporter {
  async on_session_start(): Promise<void> {
    throw new Error("exporter intentionally broken");
  }
  async on_event(): Promise<void> {
    throw new Error("exporter intentionally broken");
  }
  async on_session_end(): Promise<void> {
    throw new Error("exporter intentionally broken");
  }
}

// Canonical event stream — four event types the base adapter handles,
// mirroring the Python conformance suite.
const EVENT_STREAM: ReadonlyArray<{
  event_type: FrameworkEvent["event_type"];
  data: Record<string, unknown>;
}> = [
  { event_type: "user_prompt_submit", data: { prompt: "What is 2+2?" } },
  {
    event_type: "pre_tool_use",
    data: { tool_name: "calculator", tool_input: { expr: "2+2" } },
  },
  {
    event_type: "post_tool_use",
    data: { tool_name: "calculator", tool_response: "4" },
  },
  { event_type: "stop", data: { reason: "completed" } },
];

async function drive(mod: AdapterModule): Promise<{ count: number }> {
  for (const ev of EVENT_STREAM) await mod.adapter().emit(ev);
  return { count: EVENT_STREAM.length };
}

for (const { expectedName, module } of ADAPTER_MODULES) {
  describe(`@agentegrity/${expectedName} parity`, () => {
    afterEach(() => {
      // Each test runs against a fresh session — module-level
      // singleton persistence is the failure mode we most care about
      // catching, so reset() is asserted to actually clear state.
      module.reset();
    });

    test("adapter().adapterName matches the documented seam", () => {
      const a = module.adapter();
      expect(a.adapterName).toBe(expectedName);
    });

    test("sessionId is a stable hex/uuid string across emits", async () => {
      const before = module.adapter().sessionId;
      await drive(module);
      const after = module.adapter().sessionId;
      expect(after).toBe(before);
      expect(before.length).toBeGreaterThan(0);
      // Permit either uuid hex (32 chars) or canonical 36-char form.
      expect(/^[0-9a-f-]+$/.test(before)).toBe(true);
    });

    test("eventCount tracks emitted events", async () => {
      const { count } = await drive(module);
      expect(module.adapter().eventCount).toBe(count);
    });

    test("registered exporter receives full lifecycle", async () => {
      const exporter = new CapturingExporter();
      module.registerExporter(exporter);
      await drive(module);
      await module.adapter().end();

      const starts = exporter.calls.filter(
        (c) => c.kind === "session_start",
      );
      const events = exporter.calls.filter((c) => c.kind === "event");
      const ends = exporter.calls.filter((c) => c.kind === "session_end");
      expect(starts).toHaveLength(1);
      expect(events).toHaveLength(EVENT_STREAM.length);
      expect(ends).toHaveLength(1);

      // One session id across every callback.
      const ids = new Set(exporter.calls.map((c) => c.sessionId));
      expect(ids.size).toBe(1);
    });

    test("registerExporter is idempotent on duplicate registration", async () => {
      const exporter = new CapturingExporter();
      module.registerExporter(exporter);
      module.registerExporter(exporter);
      module.registerExporter(exporter);
      await drive(module);
      await module.adapter().end();

      // Even with three registrations, on_session_start fires exactly
      // once because the underlying registry deduplicates by identity.
      const starts = exporter.calls.filter(
        (c) => c.kind === "session_start",
      );
      expect(starts).toHaveLength(1);
    });

    test("broken exporter does not crash emit()", async () => {
      module.registerExporter(new BrokenExporter());
      // Drive must not throw despite every exporter callback raising.
      await expect(drive(module)).resolves.toBeDefined();
      expect(module.adapter().eventCount).toBe(EVENT_STREAM.length);
    });

    test("reset starts a fresh session id on next emit", async () => {
      await drive(module);
      const first = module.adapter().sessionId;
      module.reset();
      // Force a new session by emitting again.
      await module.adapter().emit({
        event_type: "user_prompt_submit",
        data: { prompt: "post-reset" },
      });
      const second = module.adapter().sessionId;
      expect(second).not.toBe(first);
    });

    test("report() returns a SessionSummary with the right shape", async () => {
      await drive(module);
      const summary = await module.report();
      expect(summary.adapter).toBe(expectedName);
      expect(typeof summary.evaluations).toBe("number");
      expect(typeof summary.events).toBe("number");
      expect(summary.events).toBe(EVENT_STREAM.length);
      expect(typeof summary.attestation_records).toBe("number");
      expect(typeof summary.chain_hash_linked).toBe("boolean");
      expect(typeof summary.enforce_mode).toBe("boolean");
    });
  });
}

describe("workspace adapter registry stability", () => {
  test("six adapter packages registered (sentinel)", () => {
    // If this fails because you added a new adapter, also add an
    // entry to ADAPTER_MODULES at the top of this file so the parity
    // matrix runs against your new adapter too.
    expect(ADAPTER_MODULES).toHaveLength(6);
    const names = new Set(ADAPTER_MODULES.map((a) => a.expectedName));
    expect(names).toEqual(
      new Set([
        "claude",
        "langchain",
        "openai_agents",
        "crewai",
        "google_adk",
        "vercel_ai",
      ]),
    );
  });
});

// ---- v0.8 multi-agent invariants ----

// Adapters that declare topology when given a multi-agent primitive.
// Mirrors Python's per-adapter Phase 4 work. Claude SDK and Vercel
// AI SDK are correctly single-agent at the framework level.
const MULTI_AGENT_CAPABLE = new Set([
  "langchain",
  "openai_agents",
  "crewai",
  "google_adk",
]);

const SINGLE_AGENT_BY_DESIGN = new Set(["claude", "vercel_ai"]);

describe("v0.8 multi-agent: topology declaration", () => {
  afterEach(() => {
    for (const { module } of ADAPTER_MODULES) {
      module.reset();
    }
  });

  for (const { expectedName, module } of ADAPTER_MODULES) {
    if (!SINGLE_AGENT_BY_DESIGN.has(expectedName)) continue;
    test(`${expectedName} stays single-agent (no topology declared)`, async () => {
      // Drive the canonical event stream — these adapters should
      // never declare a topology. The DefaultAdapter.topology
      // getter must remain null.
      await drive(module);
      const ad = module.adapter() as { topology?: unknown };
      expect(ad.topology ?? null).toBeNull();
    });
  }

  test(
    "multi-agent-capable adapters expose the setTopology API",
    () => {
      for (const { expectedName, module } of ADAPTER_MODULES) {
        if (!MULTI_AGENT_CAPABLE.has(expectedName)) continue;
        const ad = module.adapter() as {
          setTopology?: (...args: unknown[]) => Promise<void>;
          topology?: unknown;
        };
        expect(typeof ad.setTopology).toBe("function");
        // Until a multi-agent primitive is provided (graph / crew /
        // sub_agents / handoff), topology is null. The invariant
        // here is that the API surface exists, not that any
        // particular adapter auto-declares.
        expect(ad.topology ?? null).toBeNull();
      }
    },
  );
});
