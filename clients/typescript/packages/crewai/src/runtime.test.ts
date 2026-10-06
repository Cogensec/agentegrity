/**
 * Runs real @crewai-ts/core crews with an offline model, so these tests
 * fail if CrewAI stops emitting the events the adapter listens for.
 * CrewAI stores state in `node:sqlite` (Node 22+, absent from Bun), so the
 * framework is loaded only where that module exists.
 */
import { describe, test, before, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import type { FrameworkEvent } from "@agentegrity/client";
import { flush, instrument, registerExporter, report, reset } from "./index.js";

type Core = typeof import("@crewai-ts/core");
type Usage = Record<string, unknown>;

const HAS_SQLITE = await import("node:sqlite").then(() => true, () => false);

/** How the offline model answers one call. */
interface Reply {
  text: string;
  /** Usage on the provider response, reported per call by CrewAI. */
  usage?: Usage;
  /** Usage only tracked on the client, as native tool-calling paths do. */
  tracked?: Usage;
  fail?: string;
}

// Bun's node:test ignores describe() options, so the suite is skipped by name.
(HAS_SQLITE ? describe : describe.skip)("real crews", () => {
  let core: Core;
  let ScriptedLLM: new (replies: Reply[], model?: string) => InstanceType<Core["BaseLLM"]>;
  let close: (() => Promise<void>) | null = null;

  before(async () => {
    core = await import("@crewai-ts/core");
    ScriptedLLM = class extends core.BaseLLM {
      private call_ = 0;
      constructor(private readonly replies: Reply[], model = "fake-model") {
        super({ model });
      }
      call(): unknown {
        const reply = this.replies[Math.min(this.call_++, this.replies.length - 1)]!;
        if (reply.fail) throw new Error(reply.fail);
        if (reply.tracked) {
          this.trackTokenUsageInternal(reply.tracked);
          return reply.text;
        }
        return this.handleNonStreamingResponse({
          response: { choices: [{ message: { content: reply.text } }], usage: reply.usage },
        });
      }
    } as never;
  });

  beforeEach(() => reset());
  afterEach(async () => {
    await close?.();
    close = null;
  });

  const act = (text: string, usage?: Usage): Reply => ({
    text: `Thought: ping it\nAction: ping\nAction Input: ${text}`,
    usage,
  });
  const answer = (usage?: Usage): Reply => ({ text: "Thought: I now know the final answer\nFinal Answer: done", usage });

  async function kickoff(replies: Reply[], tools: unknown[] = []): Promise<void> {
    const agent = new core.Agent({ role: "pinger", goal: "ping", backstory: "b", llm: new ScriptedLLM(replies), tools } as never);
    const task = new core.Task({ description: "ping a", expectedOutput: "done", agent } as never);
    await new core.Crew({ agents: [agent], tasks: [task] } as never).kickoff();
  }

  function events(): FrameworkEvent[] {
    const seen: FrameworkEvent[] = [];
    registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
    return seen;
  }

  const ping = () =>
    core.createTool({ name: "ping", description: "ping a host", func: async (args) => `pong ${String(args.host)}` });

  test("a run reaches the adapter in order, with usage per model call", async () => {
    const seen = events();
    close = instrument(core.crewaiEventBus);
    await kickoff(
      [
        act('{"host": "a"}', { prompt_tokens: 100, completion_tokens: 10, prompt_tokens_details: { cached_tokens: 40 } }),
        answer({ prompt_tokens: 200, completion_tokens: 20, completion_tokens_details: { reasoning_tokens: 5 } }),
      ],
      [ping()],
    );
    await close();
    close = null;
    assert.deepEqual(
      seen.map((e) => e.event_type),
      ["user_prompt_submit", "task_started", "subagent_start", "pre_tool_use", "post_tool_use", "subagent_stop", "stop"],
    );
    const by = (type: string) => seen.find((e) => e.event_type === type)!.data;
    assert.deepEqual(by("pre_tool_use"), { tool_name: "ping", tool_input: { host: "a" } });
    assert.deepEqual(by("post_tool_use"), { tool_name: "ping", tool_response: "pong a" });
    assert.equal(by("subagent_start").agent_id, "pinger");
    assert.equal(by("task_started").agent_id, "pinger");
    assert.equal(by("stop").output, "Thought: I now know the final answer\nFinal Answer: done");
    const usage = by("stop").usage as Record<string, any>;
    assert.deepEqual(
      [usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.reasoning_tokens, usage.requests],
      [300, 40, 30, 5, 2],
    );
    assert.deepEqual(Object.keys(usage.by_model), ["fake-model"]);
  });

  test("anthropic usage counts the cache as input", async () => {
    close = instrument(core.crewaiEventBus);
    await kickoff([
      answer({ input_tokens: 50, cache_read_input_tokens: 900, cache_creation_input_tokens: 100, output_tokens: 5 }),
    ]);
    const usage = (await report()).usage as Record<string, number>;
    assert.deepEqual(
      [usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens, usage.output_tokens, usage.requests],
      [1050, 900, 100, 5, 1],
    );
  });

  test("calls without per-call usage are counted from the client's running total", async () => {
    close = instrument(core.crewaiEventBus);
    const tracked = (prompt: number) => ({ prompt_tokens: prompt, completion_tokens: 10, cached_tokens: 30 });
    await kickoff(
      [
        { ...act('{"host": "a"}'), usage: undefined, tracked: tracked(100) },
        { ...answer(), tracked: tracked(150) },
      ],
      [ping()],
    );
    const usage = (await report()).usage as Record<string, number>;
    assert.deepEqual([usage.input_tokens, usage.cache_read_tokens, usage.output_tokens, usage.requests], [250, 60, 20, 2]);
  });

  test("a failed kickoff stops with the error", async () => {
    const seen = events();
    close = instrument(core.crewaiEventBus);
    await assert.rejects(kickoff([{ text: "", fail: "model down" }]));
    await flush();
    const stop = seen.find((e) => e.event_type === "stop");
    assert.ok(stop, seen.map((e) => e.event_type).join(","));
    assert.match(String(stop.data.error), /model down/);
  });

  test("instrumenting twice delivers each event once, and close() stops delivery", async () => {
    const seen = events();
    const first = instrument(core.crewaiEventBus);
    close = instrument(core.crewaiEventBus);
    await kickoff([answer({ prompt_tokens: 1, completion_tokens: 1 })]);
    await flush();
    assert.equal(seen.filter((e) => e.event_type === "stop").length, 1);
    await first();
    close = null;
    const before = seen.length;
    await kickoff([answer()]);
    assert.equal(seen.length, before);
  });
});
