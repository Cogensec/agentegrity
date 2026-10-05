/**
 * Cross-runtime parity for credential fingerprints.
 *
 * These are the same vectors the Python suite asserts against. Two suites that each assert
 * their own implementation is right will drift apart unnoticed; a shared fixture turns a
 * divergence into a red build instead of zero matches on every join.
 */

import { describe, expect, it } from "bun:test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { credentialFingerprint } from "./credentials.js";

interface Vector {
  name: string;
  secret: string;
  key: string;
  expected: string;
}

// packages/client/src up five levels is the repo root.
const VECTOR_PATH = join(
  import.meta.dir,
  "../../../../../tests/fixtures/credential_fingerprint_vectors.json",
);
const vectors: Vector[] = JSON.parse(readFileSync(VECTOR_PATH, "utf8"));

describe("credentialFingerprint", () => {
  it("loads the shared vectors", () => {
    expect(vectors.length).toBeGreaterThan(0);
  });

  for (const vector of vectors) {
    it(`matches Python on: ${vector.name}`, () => {
      expect(credentialFingerprint(vector.secret, vector.key)).toBe(vector.expected);
    });
  }

  it("fails closed without a key", () => {
    expect(credentialFingerprint("sk-live-abc123", undefined)).toBeNull();
    expect(credentialFingerprint("sk-live-abc123", "")).toBeNull();
  });

  it("ignores an empty or whitespace-only secret", () => {
    expect(credentialFingerprint("", "org-key-alpha")).toBeNull();
    expect(credentialFingerprint("   \n", "org-key-alpha")).toBeNull();
  });

  it("is not a bare sha256 of the secret", () => {
    const secret = "sk-live-abc123";
    const bare = createHash("sha256").update(secret, "utf8").digest("hex");
    expect(credentialFingerprint(secret, "org-key-alpha")).not.toBe(bare);
  });

  it("isolates organizations", () => {
    expect(credentialFingerprint("sk-live-abc123", "org-a")).not.toBe(
      credentialFingerprint("sk-live-abc123", "org-b"),
    );
  });
});
