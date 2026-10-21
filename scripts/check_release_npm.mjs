#!/usr/bin/env node
/**
 * Validate the exact npm package files that would be published.
 * Uses npm pack --dry-run --ignore-scripts: no tarball is produced or uploaded.
 * Requires "bun run build" to have completed first.
 */
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = resolve(here, "..");
const workspace = join(repoRoot, "clients", "typescript", "packages");
const pyproject = readFileSync(join(repoRoot, "pyproject.toml"), "utf8");
const m = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!m) throw new Error("No canonical pyproject.toml version");
const version = m[1];
const names = ["client", "claude-sdk", "langchain", "openai-agents", "crewai", "google-adk", "vercel-ai"];
const actualNames = readdirSync(workspace).sort();
if (actualNames.join(",") !== [...names].sort().join(",")) {
  throw new Error(`Published packages changed: found ${actualNames}`);
}
const sensitive = /(?:^|\/)(?:\.env(?:\.|$)|id_rsa(?:\.|$)|[^/]+\.(?:pem|p12|key)$)/i;
for (const name of names) {
  const dir = join(workspace, name);
  const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  if (pkg.name !== `@agentegrity/${name}` || pkg.version !== version) {
    throw new Error(`${name}: wrong package name/version`);
  }
  if (name !== "client" && pkg.dependencies?.["@agentegrity/client"] !== version) {
    throw new Error(`${name}: internal client pin must be ${version}`);
  }
  const output = execFileSync(
    "npm", ["pack", "--dry-run", "--json", "--ignore-scripts"],
    { cwd: dir, encoding: "utf8", timeout: 90_000 },
  );
  const entries = JSON.parse(output);
  if (!Array.isArray(entries) || entries.length !== 1) {
    throw new Error(`${name}: npm pack did not return one package`);
  }
  const pack = entries[0];
  if (pack.name !== pkg.name || pack.version !== version) {
    throw new Error(`${name}: packed npm metadata differs from package.json`);
  }
  const files = new Set(pack.files.map((x) => x.path));
  for (const required of ["package.json", "dist/index.js", "dist/index.d.ts", "README.md"]) {
    if (!files.has(required)) throw new Error(`${name}: missing ${required} in npm pack`);
  }
  for (const file of files) {
    if (sensitive.test(file)) throw new Error(`${name}: unexpected sensitive path: ${file}`);
  }
  console.log(`OK ${pkg.name}@${version}: ${files.size} files, ${pack.size} archive bytes (dry-run)`);
}
console.log("All seven npm package candidates validated; no publishing occurred.");
