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
const expected = process.env.RELEASE_VERSION ??
  JSON.parse(readFileSync(resolve(packageDir, "client", "package.json"), "utf8")).version;
const tag = process.env.GITHUB_REF_TYPE === "tag" ? process.env.GITHUB_REF_NAME : null;
if (tag && tag !== `v${expected}`) {
  console.error(`Release tag ${tag} does not match package version ${expected}`);
  process.exit(1);
}
// Only a PR against an unchanged, already-published baseline may keep
// working after the release. The tag-driven workflow never sets this value
// and ALWAYS rejects reused registry versions.
const prBaseSha = process.env.PR_BASE_SHA || null;
if (prBaseSha && (
  process.env.GITHUB_EVENT_NAME !== "pull_request" ||
  !/^[a-f0-9]{40}$/.test(prBaseSha) ||
  tag
)) {
  console.error("PR_BASE_SHA is permitted only for a pull request base commit");
  process.exit(1);
}
const names = readdirSync(packageDir, { withFileTypes: true })
  .filter((ent) => ent.isDirectory())
  .map((ent) => ent.name)
  .sort();

if (names.length !== 7) {
  console.error(`Expected exactly 7 packages; found ${names.length}`);
  process.exit(1);
}

// Detect version-changing PRs by comparing every package against the actual
// base commit. A PR updating or rolling back a version must pass the strict
// unused-version check, even if the requested version exists on npm.
let baselineUnchanged = Boolean(prBaseSha);
if (prBaseSha) {
  for (const name of names) {
    const relativePath = `clients/typescript/packages/${name}/package.json`;
    const original = spawnSync("git", ["show", `${prBaseSha}:${relativePath}`], {
      encoding: "utf8", timeout: 10000,
    });
    if (original.error || original.status !== 0) {
      console.error(`Cannot inspect PR base manifest ${relativePath}; fail closed`);
      process.exit(1);
    }
    let basePackage;
    try {
      basePackage = JSON.parse(original.stdout);
    } catch {
      console.error(`Invalid base package metadata: ${relativePath}`);
      process.exit(1);
    }
    const current = JSON.parse(
      readFileSync(resolve(packageDir, name, "package.json"), "utf8"),
    );
    if (basePackage.name !== current.name || basePackage.version !== current.version) {
      baselineUnchanged = false;
    }
  }
}

let failed = false;
const alreadyPublished = [];
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
    alreadyPublished.push(fullName);
  } else {
    console.log(`Available: ${fullName}@${expected}`);
  }
}
if (failed) process.exit(1);

// Maintenance PRs must not be blocked by the registry version released by
// the unchanged base branch. Crucially, ALL seven packages must already
// exist. A partially published release stays a hard failure.
if (alreadyPublished.length === names.length && baselineUnchanged) {
  console.log(
    `All seven versions already published at unchanged PR base ${prBaseSha}; ` +
    "maintenance verification passed. No publication authorized.",
  );
} else if (alreadyPublished.length > 0) {
  for (const fullName of alreadyPublished) {
    console.error(`Already published: ${fullName}@${expected}`);
  }
  console.error(
    "Registry version collision or partial publication; release blocked. " +
    "New version candidates must be unused.",
  );
  process.exit(1);
} else {
  console.log("All seven npm versions are available; this does not verify publishing access.");
}
