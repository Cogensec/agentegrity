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

/**
 * Drive a single request through the reporter with a stub fetch and
 * return the headers it sent. Offline mode is disabled for the call so
 * post() actually runs the header logic.
 */
async function capturedHeaders(
  baseUrl: string,
  apiKey: string,
): Promise<Record<string, string>> {
  const prevOffline = process.env.AGENTEGRITY_OFFLINE;
  delete process.env.AGENTEGRITY_OFFLINE;
  let headers: Record<string, string> = {};
  const fetchImpl = (async (_url: string, init?: RequestInit) => {
    headers = (init?.headers as Record<string, string>) ?? {};
    return { ok: true, status: 200 } as Response;
  }) as unknown as typeof fetch;
  try {
    const r = new AgentegrityReporter({
      baseUrl,
      profile: PROFILE,
      apiKey,
      fetchImpl,
      onError: () => {},
    });
    await r.start();
  } finally {
    if (prevOffline === undefined) delete process.env.AGENTEGRITY_OFFLINE;
    else process.env.AGENTEGRITY_OFFLINE = prevOffline;
  }
  return headers;
}

// Audit L1: the Bearer token must never cross the network in cleartext.

test("token is withheld over http:// to a remote host", async () => {
  const h = await capturedHeaders("http://example.com:8787", "secret-token");
  assert.equal(h.authorization, undefined);
});

test("token is sent over https://", async () => {
  const h = await capturedHeaders("https://example.com", "secret-token");
  assert.equal(h.authorization, "Bearer secret-token");
});

test("token is sent over http://localhost (loopback)", async () => {
  const h = await capturedHeaders("http://localhost:8787", "secret-token");
  assert.equal(h.authorization, "Bearer secret-token");
});

test("token is sent over http://127.0.0.1 (loopback)", async () => {
  const h = await capturedHeaders("http://127.0.0.1:8787", "secret-token");
  assert.equal(h.authorization, "Bearer secret-token");
});

test("constructor warns once when apiKey is set on an unsafe URL", () => {
  const errs: string[] = [];
  new AgentegrityReporter({
    baseUrl: "http://example.com",
    profile: PROFILE,
    apiKey: "secret-token",
    onError: (_e, step) => errs.push(step),
  });
  assert.deepEqual(errs, ["config"]);
});
