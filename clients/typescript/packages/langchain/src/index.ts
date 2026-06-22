/**
 * `@agentegrity/langchain` — zero-config adapter for LangChain JS and
 * LangGraph JS. Mirrors the Python `agentegrity.langchain` module 1:1.
 *
 * Usage:
 *
 * ```ts
 * import { ChatAnthropic } from "@langchain/anthropic";
 * import { instrument, report } from "@agentegrity/langchain";
 *
 * const llm = new ChatAnthropic({ callbacks: [instrument()] });
 * // ... run chain / graph ...
 * console.log(await report());
 * ```
 *
 * LangChain's callback system propagates down into every tool, chain,
 * sub-chain, and LLM call — so wiring it once at the top is enough.
 */

import {
  AgentMember,
  AgentRole,
  AgentTopology,
  TopologyKind,
  createDefaultAdapter,
  type AgentProfile,
  type DefaultAdapter,
  type SessionExporter,
  type SessionSummary,
} from "@agentegrity/client";

import { AgentegrityLangChainHandler } from "./handler.js";

let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) {
    _default = createDefaultAdapter({ adapterName: "langchain" });
  }
  return _default;
}

export interface InstrumentOptions {
  profile?: Partial<AgentProfile>;
  /** Reserved for future enforce semantics. Accepted but ignored in v0.5.0. */
  enforce?: boolean;
}

/**
 * Build a LangChain callback handler wired to the default agentegrity
 * adapter. Pass it via `callbacks: [instrument()]` when constructing
 * your model, chain, or graph — LangChain propagates callbacks to
 * every child runnable automatically.
 */
export function instrument(options: InstrumentOptions = {}): AgentegrityLangChainHandler {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: "langchain", profile: options.profile })
    : defaultAdapter();
  return new AgentegrityLangChainHandler(ad);
}

export async function report(): Promise<SessionSummary> {
  if (_default === null) {
    return {
      adapter: "langchain",
      agent_id: null,
      evaluations: 0,
      events: 0,
      attestation_records: 0,
      chain_valid: true,
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

/**
 * Instrument a compiled LangGraph (v0.8 multi-agent).
 *
 * Returns the agentegrity callback handler AND walks the graph's
 * node list via `graph.getGraph().nodes` to declare an
 * AgentTopology. Heuristic for kind: a node named `supervisor` /
 * `supervisor_agent` / `orchestrator` → HIERARCHICAL_DAG with
 * that node as SUPERVISOR; otherwise PEER_TO_PEER with all nodes
 * as PEER. Falls back to single-agent (no topology declaration)
 * if `getGraph` isn't callable.
 *
 * Mirrors Python `agentegrity.langchain.LangChainAdapter.instrument_graph`.
 */
export function instrumentGraph(
  graph: unknown,
  options: InstrumentOptions = {},
): AgentegrityLangChainHandler {
  const handler = instrument(options);
  const ad = adapter();
  try {
    declareGraphTopology(graph, ad);
  } catch (err) {
    // Introspection failure is non-fatal; the handler still works
    // as a single-agent instrumentation.
    // eslint-disable-next-line no-console
    console.warn(
      "[agentegrity:langchain] instrumentGraph could not introspect topology:",
      err,
    );
  }
  return handler;
}

function declareGraphTopology(graph: unknown, ad: DefaultAdapter): void {
  const getGraph = (graph as { getGraph?: () => { nodes?: unknown } } | null)
    ?.getGraph;
  if (typeof getGraph !== "function") return;
  const graphObj = getGraph.call(graph);
  const nodes = (graphObj as { nodes?: Record<string, unknown> | Map<string, unknown> })
    ?.nodes;
  if (!nodes) return;

  const nodeKeys: string[] = [];
  const sink = (k: string) => {
    if (k && !k.startsWith("__")) nodeKeys.push(k);
  };
  if (nodes instanceof Map) {
    for (const k of nodes.keys()) sink(String(k));
  } else if (typeof nodes === "object") {
    for (const k of Object.keys(nodes)) sink(k);
  }
  if (nodeKeys.length === 0) return;

  const supervisorKey = nodeKeys.find((k) => {
    const lower = k.toLowerCase();
    return lower === "supervisor" || lower === "supervisor_agent" || lower === "orchestrator";
  });

  let topology: AgentTopology;
  let myRole: AgentRole;
  if (supervisorKey !== undefined) {
    const members: AgentMember[] = [
      new AgentMember({
        agentId: supervisorKey,
        name: supervisorKey,
        role: AgentRole.SUPERVISOR,
        capabilities: ["tool_use"],
      }),
    ];
    for (const k of nodeKeys) {
      if (k === supervisorKey) continue;
      members.push(
        new AgentMember({
          agentId: k,
          name: k,
          role: AgentRole.WORKER,
          parentId: supervisorKey,
          capabilities: ["tool_use"],
        }),
      );
    }
    topology = new AgentTopology({
      kind: TopologyKind.HIERARCHICAL_DAG,
      members,
      commChannels: new Set(["peer_messages"]),
    });
    myRole = AgentRole.SUPERVISOR;
  } else {
    const members: AgentMember[] = nodeKeys.map(
      (k) =>
        new AgentMember({
          agentId: k,
          name: k,
          role: AgentRole.PEER,
          capabilities: ["tool_use"],
        }),
    );
    topology = new AgentTopology({
      kind: TopologyKind.PEER_TO_PEER,
      members,
      commChannels: new Set(["peer_messages"]),
    });
    myRole = AgentRole.PEER;
  }
  void ad.setTopology(topology, myRole);
}

export { AgentegrityLangChainHandler };
export type { SessionExporter };
