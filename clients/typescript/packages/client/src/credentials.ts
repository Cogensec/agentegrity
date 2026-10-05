/**
 * Credential fingerprints: identify a credential without holding it.
 *
 * This MUST produce byte-identical output to `agentegrity.fingerprint` in Python. A
 * divergent implementation returns zero matches, which is indistinguishable from "no leaks
 * found". Both runtimes assert against `tests/fixtures/credential_fingerprint_vectors.json`,
 * so neither can drift without a red build.
 *
 * HMAC rather than a bare SHA-256: an unkeyed digest of a credential is globally joinable,
 * and structured keys (`sk-`, `ghp_`, `AKIA`) are cheap to brute-force offline.
 */

import { createHmac } from "node:crypto";

/** A credential an agent uses, identified by fingerprint and a name, never its value. */
export interface CredentialRef {
  provider: string;
  /** A name such as `openai/api_key` or an env var name, never a sample of the secret. */
  label: string;
  fingerprint: string;
}

/** Keyed fingerprint of a credential, or null when no key is given or the secret is empty. */
export function credentialFingerprint(
  secret: string,
  key: string | undefined | null,
): string | null {
  // Fail closed: with no key, return nothing instead of downgrading to an unkeyed hash.
  if (!key) return null;
  // A scanner reads the secret out of a repo without the file's trailing newline.
  const normalized = secret.trim();
  if (!normalized) return null;
  return createHmac("sha256", key).update(normalized, "utf8").digest("hex");
}
