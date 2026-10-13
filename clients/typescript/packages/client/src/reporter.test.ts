import { test } from "node:test";
import assert from "node:assert/strict";
import { AgentegrityReporter } from "./reporter.js";
import type { AgentProfile } from "./types.js";

const PROFILE: AgentProfile = {
  agent_id: "a1",
  name: "t",
  agent_type: "tool_using",
  capabilities: [],
  deployment_context: "cloud",
  risk_tier: "low",
};

interface Sent {
  calls: number;
  headers: Record<string, string>;
  init?: RequestInit;
  errors: string[];
}

/**
 * Drive a session start through the reporter with a stub fetch and return
 * what it sent. Offline mode is disabled for the call so post() runs.
 */
async function send(baseUrl: string | undefined, apiKey?: string): Promise<Sent> {
  const prevOffline = process.env.AGENTEGRITY_OFFLINE;
  delete process.env.AGENTEGRITY_OFFLINE;
  const sent: Sent = { calls: 0, headers: {}, errors: [] };
  const fetchImpl = (async (_url: string, init?: RequestInit) => {
    sent.calls += 1;
    sent.init = init;
    sent.headers = (init?.headers as Record<string, string>) ?? {};
    return { ok: true, status: 200 } as Response;
  }) as unknown as typeof fetch;
  try {
    const r = new AgentegrityReporter({
      baseUrl,
      profile: PROFILE,
      apiKey,
      fetchImpl,
      onError: (_e, step) => sent.errors.push(step),
    });
    await r.start();
  } finally {
    if (prevOffline === undefined) delete process.env.AGENTEGRITY_OFFLINE;
    else process.env.AGENTEGRITY_OFFLINE = prevOffline;
  }
  return sent;
}

// Session data and the token travel only over HTTPS or stay on this machine.

test("nothing is sent to http:// on a remote host", async () => {
  const sent = await send("http://example.com:8787", "secret-token");
  assert.equal(sent.calls, 0);
  assert.deepEqual(sent.errors, ["config"]);
});

test("nothing is sent without a base URL", async () => {
  const sent = await send(undefined, "secret-token");
  assert.equal(sent.calls, 0);
  assert.deepEqual(sent.errors, []);
});

test("token is sent over https://", async () => {
  const sent = await send("https://example.com", "secret-token");
  assert.equal(sent.headers.authorization, "Bearer secret-token");
});

test("token is sent over http://localhost (loopback)", async () => {
  const sent = await send("http://localhost:8787", "secret-token");
  assert.equal(sent.headers.authorization, "Bearer secret-token");
});

test("token is sent over http://127.0.0.1 (loopback)", async () => {
  const sent = await send("http://127.0.0.1:8787", "secret-token");
  assert.equal(sent.headers.authorization, "Bearer secret-token");
});

test("redirects are refused, so the token never follows one", async () => {
  const sent = await send("https://example.com", "secret-token");
  assert.equal(sent.init?.redirect, "error");
});
