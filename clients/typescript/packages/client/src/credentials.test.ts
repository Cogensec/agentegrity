/**
 * Cross-runtime parity for credential fingerprints.
 *
 * These are the same vectors the Python suite asserts against. Two suites that each assert
 * their own implementation is right will drift apart unnoticed; a shared fixture turns a
 * divergence into a red build instead of zero matches on every join.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { credentialFingerprint } from "./credentials.js";

interface Vector {
  name: string;
  secret: string;
  key: string;
  expected: string;
}

// packages/client/src up five levels is the repo root.
const VECTOR_PATH = fileURLToPath(
  new URL("../../../../../tests/fixtures/credential_fingerprint_vectors.json", import.meta.url),
);
const vectors: Vector[] = JSON.parse(readFileSync(VECTOR_PATH, "utf8"));

test("credentialFingerprint loads the shared vectors", () => {
  assert.ok(vectors.length > 0);
});

for (const vector of vectors) {
  test(`credentialFingerprint matches Python on: ${vector.name}`, () => {
    assert.equal(credentialFingerprint(vector.secret, vector.key), vector.expected);
  });
}

test("credentialFingerprint fails closed without a key", () => {
  assert.equal(credentialFingerprint("sk-live-abc123", undefined), null);
  assert.equal(credentialFingerprint("sk-live-abc123", ""), null);
});

test("credentialFingerprint ignores an empty or whitespace-only secret", () => {
  assert.equal(credentialFingerprint("", "org-key-alpha"), null);
  assert.equal(credentialFingerprint("   \n", "org-key-alpha"), null);
});

test("credentialFingerprint is not a bare sha256 of the secret", () => {
  const secret = "sk-live-abc123";
  const bare = createHash("sha256").update(secret, "utf8").digest("hex");
  assert.notEqual(credentialFingerprint(secret, "org-key-alpha"), bare);
});

test("credentialFingerprint isolates organizations", () => {
  assert.notEqual(
    credentialFingerprint("sk-live-abc123", "org-a"),
    credentialFingerprint("sk-live-abc123", "org-b"),
  );
});
