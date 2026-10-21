#!/usr/bin/env node
/**
 * Preflight for one immutable npm release version across all workspace packages.
 * Check all seven versions before publishing anything to PyPI / TestPyPI.
 * This is a publicly readable registry metadata check; no credentials required.
 * Any invalid/unavailable response fails closed rather than assuming availability.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const packageDir = resolve(root, "clients/typescript/packages");
const expected = process.env.RELEASE_VERSION ?? "0.13.0";
const names = readdirSync(packageDir, { withFileTypes: true })
  .filter((ent) => ent.isDirectory())
  .map((ent) => ent.name)
  .sort();

if (names.length !== 7) {
  console.error(`Expected exactly 7 packages; found ${names.length}`);
  process.exit(1);
}

let failed = false;
for (const name of names) {
  const json = JSON.parse(
    readFileSync(resolve(packageDir, name, "package.json"), "utf8"),
  );
  const fullName = json.name;
  if (json.version !== expected || !fullName?.startsWith("@agentegrity/")) {
    console.error(`Invalid name/version: ${fullName}@${json.version}; expected ${expected}`);
    failed = true;
    continue;
  }
  const result = spawnSync(
    "npm",
    ["view", fullName, "versions", "--json", "--registry=https://registry.npmjs.org/"],
    { encoding: "utf8", timeout: 30000 },
  );
  if (result.error || result.status !== 0) {
    console.error(`Cannot verify npm registry history for ${fullName}; fail closed`);
    failed = true;
    continue;
  }
  let versions;
  try {
    const parsed = JSON.parse(result.stdout);
    versions = Array.isArray(parsed) ? parsed : [parsed];
    if (!versions.every((v) => typeof v === "string")) throw Error("invalid versions");
  } catch {
    console.error(`Invalid npm registry version response for ${fullName}`);
    failed = true;
    continue;
  }
  if (versions.includes(expected)) {
    console.error(`Already published: ${fullName}@${expected}; abort the release`);
    failed = true;
  } else {
    console.log(`Available: ${fullName}@${expected}`);
  }
}
if (failed) process.exit(1);
console.log("All seven npm versions are available; this does not verify publishing access.");
