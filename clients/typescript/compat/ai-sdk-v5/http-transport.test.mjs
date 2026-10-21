/**
 * Research-only HTTP transport compatibility canary for the real transitive
 * Undici selected by AI SDK v5's provider-utils package.
 *
 * The localhost tests run under both published Undici 5 and experimental
 * Undici 6 dependency resolutions. These tests do NOT establish that all
 * production AI SDK v5 providers, streams, redirects, proxies, or multipart
 * security properties are compatible with a cross-major override.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const providerRequire = createRequire(require.resolve("@ai-sdk/provider-utils"));
const { fetch, request, FormData } = providerRequire("undici");

async function readBody(stream) {
  const chunks = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks);
}

async function withServer(handler, action) {
  let handlerError;
  const server = createServer(async (req, res) => {
    try {
      await handler(req, res);
    } catch (error) {
      // The listener is loopback-only, but never serialize stack traces
      // or internal test errors into a network response.
      handlerError = error;
      res.statusCode = 500;
      res.end("test request failed");
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Expected a localhost TCP listener");
  }
  try {
    await action(`http://127.0.0.1:${address.port}`);
    if (handlerError) throw handlerError;
  } finally {
    server.closeAllConnections();
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }
}

test("provider-utils Undici can POST and parse real localhost JSON", async () => {
  await withServer(
    async (req, res) => {
      assert.equal(req.method, "POST");
      assert.equal(req.url, "/json");
      assert.match(String(req.headers["content-type"]), /application\/json/);
      assert.deepEqual(JSON.parse((await readBody(req)).toString()), { prompt: "hello" });
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ accepted: true, value: 42 }));
    },
    async (origin) => {
      const response = await request(`${origin}/json`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: "hello" }),
      });
      assert.equal(response.statusCode, 200);
      assert.deepEqual(await response.body.json(), { accepted: true, value: 42 });
    },
  );
});

test("provider-utils Undici encodes multipart uploads with a real boundary", async () => {
  await withServer(
    async (req, res) => {
      assert.equal(req.method, "POST");
      const contentType = String(req.headers["content-type"]);
      assert.match(contentType, /^multipart\/form-data; boundary=/);
      const boundary = contentType.split("boundary=")[1];
      const body = (await readBody(req)).toString();
      assert.ok(body.includes(`--${boundary}`));
      assert.match(body, /name="metadata"/);
      assert.match(body, /sample-value/);
      res.statusCode = 201;
      res.end("uploaded");
    },
    async (origin) => {
      const data = new FormData();
      data.append("metadata", "sample-value");
      const response = await fetch(`${origin}/multipart`, {
        method: "POST",
        body: data,
      });
      assert.equal(response.status, 201);
      assert.equal(await response.text(), "uploaded");
    },
  );
});

test("provider-utils Undici supports streamed localhost HTTP responses", async () => {
  await withServer(
    async (_req, res) => {
      res.setHeader("content-type", "text/plain");
      res.write("one-");
      setImmediate(() => res.end("two"));
    },
    async (origin) => {
      const response = await fetch(`${origin}/stream`);
      assert.equal(response.status, 200);
      const reader = response.body.getReader();
      const parts = [];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parts.push(Buffer.from(value));
      }
      assert.equal(Buffer.concat(parts).toString(), "one-two");
    },
  );
});

test("provider-utils Undici aborts an active HTTP request and closes its socket", {
  timeout: 15000,
}, async () => {
  // Unlike node:test timeout, these races reject the awaited operation,
  // letting withServer's finally close all connections on a stalled path.
  async function bounded(promise, description) {
    let timer;
    try {
      return await Promise.race([
        promise,
        new Promise((_resolve, reject) => {
          timer = setTimeout(
            () => reject(new Error(`Timed out waiting for ${description}`)),
            4000,
          );
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }
  // Prove the request actually reached a server. Pre-aborting a signal
  // shortcuts fetch without exercising transport cancellation or cleanup.
  let receivedRequest;
  let closedConnection;
  const received = new Promise((resolve) => { receivedRequest = resolve; });
  const closed = new Promise((resolve) => { closedConnection = resolve; });

  await withServer(
    async (_req, res) => {
      // Hold the HTTP response open. The only expected reason for "close"
      // is the client's in-flight abort, not a normal response finish.
      res.once("close", closedConnection);
      receivedRequest();
    },
    async (origin) => {
      const aborter = new AbortController();
      // Attach rejection handling immediately; even an aborted request
      // that settles after the timeout must not become an unhandled error.
      const pending = fetch(`${origin}/in-flight-abort`, {
        signal: aborter.signal,
      }).then(
        () => ({ fulfilled: true }),
        (error) => ({ fulfilled: false, error }),
      );
      try {
        await bounded(received, "the server to receive the request");
        aborter.abort();
        const outcome = await bounded(pending, "the fetch abort rejection");
        assert.equal(outcome.fulfilled, false);
        assert.match(String(outcome.error), /abort/i);
        await bounded(closed, "the aborted socket to close");
      } finally {
        aborter.abort();
      }
    },
  );
});
