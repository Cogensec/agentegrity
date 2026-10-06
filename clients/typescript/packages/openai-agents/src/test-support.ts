/** Shared by the test files: a scripted offline model and an event collector. */
import { Usage, tool, type Model, type ModelResponse } from "@openai/agents";
import { z } from "zod";
import type { FrameworkEvent } from "@agentegrity/client";
import { registerExporter } from "./index.js";

export type Output = ModelResponse["output"];

export const call = (name: string, args = "{}", id = name): Output[number] =>
  ({ type: "function_call", callId: id, name, arguments: args, status: "completed" }) as Output[number];
export const say = (text: string): Output[number] =>
  ({
    type: "message",
    role: "assistant",
    status: "completed",
    content: [{ type: "output_text", text }],
  }) as Output[number];

/** A model that replays scripted outputs and reports fixed usage per call. */
export function scripted(
  steps: Output[],
  usage: { input: number; output: number; cached?: number; reasoning?: number },
): Model {
  let i = 0;
  return {
    async getResponse() {
      const step = steps[Math.min(i, steps.length - 1)]!;
      i++;
      return {
        output: step,
        responseId: `resp_${i}`,
        usage: new Usage({
          requests: 1,
          inputTokens: usage.input,
          outputTokens: usage.output,
          totalTokens: usage.input + usage.output,
          inputTokensDetails: { cached_tokens: usage.cached ?? 0 },
          outputTokensDetails: { reasoning_tokens: usage.reasoning ?? 0 },
        }),
      };
    },
    async *getStreamedResponse() {
      throw new Error("not used");
    },
  } as Model;
}

export const ping = tool({
  name: "ping",
  description: "ping",
  parameters: z.object({ host: z.string() }),
  execute: async ({ host }) => `pong ${host}`,
});

export function events(): FrameworkEvent[] {
  const seen: FrameworkEvent[] = [];
  registerExporter({ on_event: (_sid, ev) => void seen.push(ev) });
  return seen;
}
