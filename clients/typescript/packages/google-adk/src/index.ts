/**
 * `@agentegrity/google-adk` — zero-config adapter for the Google Agent
 * Development Kit for TypeScript (`@google/adk`).
 *
 * ```ts
 * import { InMemoryRunner, LlmAgent } from "@google/adk";
 * import { instrument, report } from "@agentegrity/google-adk";
 *
 * const agent = new LlmAgent({ name: "my-agent", model: "gemini-2.5-flash" });
 * const close = instrument(agent);
 * const runner = new InMemoryRunner({ agent, appName: "app" });
 * // ... runner.runAsync({ userId, sessionId, newMessage }) ...
 * await close();
 * console.log(await report());
 * ```
 *
 * `instrument()` adds callbacks to the agent and to every agent reachable
 * from it (`subAgents`, and agents wrapped as tools), ahead of any callbacks
 * already set. ADK stops at the first callback that returns a value; these
 * return nothing, so the user's callbacks still run and decide.
 *
 * Event mapping:
 *
 *   beforeAgentCallback  -> user_prompt_submit (instrumented agent)
 *                           subagent_start (any other agent)
 *   afterAgentCallback   -> stop / subagent_stop
 *   beforeToolCallback   -> pre_tool_use
 *   afterToolCallback    -> post_tool_use
 *   afterModelCallback   -> token usage (final responses; partials skipped)
 */

import { randomUUID } from "node:crypto";
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
  type TokenUsage,
} from "@agentegrity/client";

/**
 * Walk the ADK agent's sub_agents/subAgents at instrument time and
 * declare a HIERARCHICAL_DAG topology. Plain Agent without
 * sub_agents → no topology (correctly single-agent).
 *
 * Mirrors Python `agentegrity.google_adk.GoogleADKAdapter._maybe_declare_workflow_topology`.
 */
function maybeDeclareWorkflowTopology(
  agent: { sub_agents?: unknown; subAgents?: unknown; name?: string; [k: string]: unknown },
  ad: DefaultAdapter,
): void {
  const subAgents = (agent.subAgents ?? agent.sub_agents) as unknown[] | undefined;
  if (!Array.isArray(subAgents) || subAgents.length === 0) return;

  const supervisorId = String(agent.name ?? "workflow_agent");
  const members: AgentMember[] = [
    new AgentMember({
      agentId: supervisorId,
      name: supervisorId,
      role: AgentRole.SUPERVISOR,
      capabilities: ["tool_use"],
    }),
  ];
  for (const sub of subAgents) {
    const subRecord = (sub ?? {}) as { name?: string };
    const subId = String(subRecord.name ?? "sub_agent");
    members.push(
      new AgentMember({
        agentId: subId,
        name: subId,
        role: AgentRole.WORKER,
        parentId: supervisorId,
        capabilities: ["tool_use"],
      }),
    );
  }
  const topology = new AgentTopology({
    kind: TopologyKind.HIERARCHICAL_DAG,
    members,
    commChannels: new Set(),
  });
  void ad.setTopology(topology, AgentRole.SUPERVISOR);
}


type Context = {
  eventActions?: object;
  invocationContext?: { agent?: { name?: string; canonicalModel?: { model?: unknown } } };
};
type Callback = (...args: any[]) => unknown;
type Usage = Record<string, unknown>;

/** The parts of an ADK agent the adapter touches. */
export interface AdkAgentLike {
  name?: string;
  subAgents?: unknown[];
  sub_agents?: unknown[];
  tools?: unknown[];
  beforeAgentCallback?: Callback[];
  afterAgentCallback?: Callback[];
  beforeToolCallback?: Callback | Callback[];
  afterToolCallback?: Callback | Callback[];
  afterModelCallback?: Callback | Callback[];
  [k: string]: unknown;
}

const instrumented = new WeakSet<object>();
const callKeys = new WeakMap<object, string>();

function prepend(existing: Callback | Callback[] | undefined, ours: Callback): Callback[] {
  return [ours, ...(Array.isArray(existing) ? existing : existing ? [existing] : [])];
}

function count(usage: Usage, key: string): number | null {
  const value = usage[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Normalize Gemini usage metadata: the prompt count already includes cached
 * tokens; tool-use prompt and thinking tokens are reported beside it.
 */
function modelUsage(usage: Usage): TokenUsage {
  const thoughts = count(usage, "thoughtsTokenCount");
  return {
    input_tokens: (count(usage, "promptTokenCount") ?? 0) + (count(usage, "toolUsePromptTokenCount") ?? 0),
    output_tokens: (count(usage, "candidatesTokenCount") ?? 0) + (thoughts ?? 0),
    cache_read_tokens: count(usage, "cachedContentTokenCount"),
    reasoning_tokens: thoughts,
  };
}

function nameOf(agent: unknown): string {
  return String((agent as { name?: string } | null)?.name ?? "agent");
}

/** Agents reachable from `root`: sub-agents and agents wrapped as tools. */
function reachable(root: AdkAgentLike): AdkAgentLike[] {
  const found: AdkAgentLike[] = [];
  const visit = (agent: unknown) => {
    if (!agent || typeof agent !== "object" || found.includes(agent as AdkAgentLike)) return;
    found.push(agent as AdkAgentLike);
    const a = agent as AdkAgentLike;
    for (const sub of a.subAgents ?? a.sub_agents ?? []) visit(sub);
    for (const tool of a.tools ?? []) visit((tool as { agent?: unknown } | null)?.agent);
  };
  visit(root);
  return found;
}

function attach(agent: AdkAgentLike, ad: DefaultAdapter, isRoot: boolean): void {
  if (instrumented.has(agent)) return;
  instrumented.add(agent);
  if (Array.isArray(agent.beforeAgentCallback)) {
    agent.beforeAgentCallback.unshift(async (context: Context) => {
      const name = nameOf(context?.invocationContext?.agent ?? agent);
      await ad.emit(isRoot
        ? { event_type: "user_prompt_submit", data: { agent: name } }
        : { event_type: "subagent_start", data: { agent_id: name } });
      return undefined;
    });
  }
  if (Array.isArray(agent.afterAgentCallback)) {
    agent.afterAgentCallback.unshift(async () => {
      await ad.emit(isRoot
        ? { event_type: "stop", data: { agent: nameOf(agent) } }
        : { event_type: "subagent_stop", data: { agent_id: nameOf(agent) } });
      return undefined;
    });
  }
  // Workflow agents have no model or tool callbacks and reject unknown fields.
  if (!("afterModelCallback" in agent)) return;
  agent.beforeToolCallback = prepend(agent.beforeToolCallback, async ({ tool, args }: { tool: unknown; args: unknown }) => {
    await ad.emit({ event_type: "pre_tool_use", data: { tool_name: nameOf(tool), tool_input: args ?? {} } });
    return undefined;
  });
  agent.afterToolCallback = prepend(agent.afterToolCallback, async ({ tool, response }: { tool: unknown; response: unknown }) => {
    await ad.emit({ event_type: "post_tool_use", data: { tool_name: nameOf(tool), tool_response: response } });
    return undefined;
  });
  agent.afterModelCallback = prepend(agent.afterModelCallback, ({ context, response }: { context: Context; response: { partial?: boolean; usageMetadata?: Usage } }) => {
    const usage = response?.usageMetadata;
    if (!usage || response.partial === true) return undefined;
    // Every response of one model call shares its eventActions, so the last one wins.
    const call = context?.eventActions ?? response;
    let key = callKeys.get(call);
    if (!key) callKeys.set(call, (key = `google-adk:${randomUUID()}`));
    const model = context?.invocationContext?.agent?.canonicalModel?.model;
    ad.recordUsage(key, typeof model === "string" ? model : null, modelUsage(usage), { source: "provider_response" });
    return undefined;
  });
}

let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) _default = createDefaultAdapter({ adapterName: "google_adk" });
  return _default;
}

export interface InstrumentOptions {
  profile?: Partial<AgentProfile>;
}

/**
 * Add agentegrity callbacks to an ADK agent and every agent reachable from
 * it. Instrumenting an agent again is a no-op. Returns a function that ends
 * the session.
 */
export function instrument(agent: AdkAgentLike, options: InstrumentOptions = {}): () => Promise<void> {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: "google_adk", profile: options.profile })
    : defaultAdapter();
  maybeDeclareWorkflowTopology(agent, ad);
  for (const reached of reachable(agent)) attach(reached, ad, reached === agent);
  return async () => {
    await ad.end();
  };
}

export async function report(): Promise<SessionSummary> {
  if (_default === null) {
    return {
      adapter: "google_adk",
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

export function adapter(): DefaultAdapter {
  return defaultAdapter();
}

export type { SessionExporter };
