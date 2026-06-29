/**
 * `@agentegrity/openai-agents` — zero-config adapter for the OpenAI
 * Agents JS SDK (`@openai/agents`). Mirrors the Python
 * `agentegrity.openai_agents` module 1:1.
 *
 * Usage:
 *
 * ```ts
 * import { Agent, Runner } from "@openai/agents";
 * import { runHooks, report } from "@agentegrity/openai-agents";
 *
 * await Runner.run(agent, "hello", { hooks: runHooks() });
 * console.log(await report());
 * ```
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

function agentIdOf(agent: unknown): string {
  const a = agent as { name?: string; id?: string } | null;
  return String(a?.name ?? a?.id ?? "agent");
}

async function seedTopologyFromInitial(
  ad: DefaultAdapter,
  agentId: string,
): Promise<void> {
  if (ad.topology !== null) {
    if (ad.topology.member(agentId) !== null) return;
  }
  const member = new AgentMember({
    agentId,
    name: agentId,
    role: AgentRole.PEER,
    capabilities: ["tool_use"],
  });
  const topology = new AgentTopology({
    kind: TopologyKind.PEER_TO_PEER,
    members: [member],
    commChannels: new Set(["peer_messages"]),
  });
  await ad.setTopology(topology, AgentRole.PEER);
}

async function addHandoffTarget(
  ad: DefaultAdapter,
  agentId: string,
): Promise<void> {
  const existing = ad.topology;
  if (existing === null) {
    await seedTopologyFromInitial(ad, agentId);
    return;
  }
  if (existing.member(agentId) !== null) return;
  const next = existing.withMember(
    new AgentMember({
      agentId,
      name: agentId,
      role: AgentRole.PEER,
      capabilities: ["tool_use"],
    }),
  );
  await ad.setTopology(next, AgentRole.PEER);
}

let _default: DefaultAdapter | null = null;

function defaultAdapter(): DefaultAdapter {
  if (_default === null) {
    _default = createDefaultAdapter({ adapterName: "openai_agents" });
  }
  return _default;
}

export interface RunHooksOptions {
  profile?: Partial<AgentProfile>;
  enforce?: boolean;
}

/**
 * Build a RunHooks-shaped object for the OpenAI Agents JS runner.
 * The hook names (`onAgentStart`, `onToolStart`, `onToolEnd`,
 * `onAgentFinish`) match the published SDK contract as of 0.0.x.
 */
export function runHooks(options: RunHooksOptions = {}): Record<string, unknown> {
  const ad = options.profile
    ? createDefaultAdapter({ adapterName: "openai_agents", profile: options.profile })
    : defaultAdapter();

  return {
    onAgentStart: async (_ctx: unknown, agentInfo: unknown) => {
      // v0.8: seed a PEER_TO_PEER topology with this agent as the
      // single PEER. Handoffs grow the topology incrementally.
      await seedTopologyFromInitial(ad, agentIdOf(agentInfo));
      await ad.emit({
        event_type: "user_prompt_submit",
        data: { agent: agentInfo },
      });
    },
    onToolStart: async (_ctx: unknown, _agentInfo: unknown, tool: unknown) => {
      const t = (tool ?? {}) as { name?: string; input?: unknown };
      await ad.emit({
        event_type: "pre_tool_use",
        data: { tool_name: t.name ?? "unknown", tool_input: t.input },
      });
    },
    onToolEnd: async (
      _ctx: unknown,
      _agentInfo: unknown,
      tool: unknown,
      result: unknown,
    ) => {
      const t = (tool ?? {}) as { name?: string };
      await ad.emit({
        event_type: "post_tool_use",
        data: { tool_name: t.name ?? "unknown", tool_response: result },
      });
    },
    onAgentFinish: async (_ctx: unknown, _agentInfo: unknown, output: unknown) => {
      await ad.emit({
        event_type: "stop",
        data: { output },
      });
    },
    onHandoff: async (_ctx: unknown, fromAgent: unknown, toAgent: unknown) => {
      // v0.8: append the handoff target as a PEER, growing the
      // PEER_TO_PEER topology and emitting topology_change.
      const toId = agentIdOf(toAgent);
      await addHandoffTarget(ad, toId);
      await ad.emit({
        event_type: "subagent_start",
        data: { from: fromAgent, to: toAgent, handoff_to: toId },
      });
    },
  };
}

export async function report(): Promise<SessionSummary> {
  if (_default === null) {
    return {
      adapter: "openai_agents",
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
