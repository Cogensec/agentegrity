#!/usr/bin/env node
/**
 * Release gate for verified security patch floors in the Bun workspace.
 *
 * This intentionally does NOT claim that the entire optional framework
 * dependency graph is vulnerability-free. Undici 5 / Busboy 2 (AI SDK v5
 * dev compatibility) and the Python CrewAI optional extras remain tracked
 * separately in docs/releases/v0.13.0-security-triage.md.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const lock = readFileSync(join(root, "bun.lock"), "utf8");
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

function tuple(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(version);
  if (!match) throw new Error(`Unrecognized package version: ${version}`);
  return match.slice(1).map(Number);
}

function atLeast(version, floor) {
  const a = tuple(version);
  const b = tuple(floor);
  for (let index = 0; index < 3; index++) {
    if (a[index] > b[index]) return true;
    if (a[index] < b[index]) return false;
  }
  return true;
}

function scan(name) {
  const expr = new RegExp(
    `^\\s+"[^"]*${name}": \\["${name}@(\\d+\\.\\d+\\.\\d+)"`,
    "gm",
  );
  return [...lock.matchAll(expr)].map((match) => match[1]);
}

const failures = [];
function requirePatch(name, floor, major) {
  const copies = scan(name).filter((version) => tuple(version)[0] === major);
  if (copies.length === 0) {
    failures.push(`No ${name} ${major}.x entry found in bun.lock`);
  } else {
    for (const version of copies) {
      if (!atLeast(version, floor)) {
        failures.push(`Vulnerable ${name}@${version} below ${floor}`);
      } else {
        console.log(`✓ ${name}@${version} >= ${floor}`);
      }
    }
  }
}

requirePatch("undici", "6.29.1", 6);
requirePatch("undici", "7.30.1", 7);
requirePatch("esbuild", "0.28.1", 0);
requirePatch("tsx", "4.23.9", 4);

for (const [range, patched] of [
  ["undici@^6.28.0", "^6.29.1"],
  ["undici@^7.29.0", "^7.30.1"],
]) {
  if (manifest.overrides?.[range] !== patched) {
    failures.push(`Missing scoped range override ${range} -> ${patched}`);
  }
}

// Deliberately not a passing verdict for undici 5 / Busboy 2. These remain
// upstream-constrained optional dev dependencies and a separate release gate.
if (scan("undici").some((v) => tuple(v)[0] === 5)) {
  console.log("! Legacy undici 5.x present (AI SDK v5 test path); unresolved");
}

if (failures.length) {
  for (const failure of failures) console.error("✗ " + failure);
  process.exit(1);
}

console.log("Verified scoped patch floors; optional framework audits remain open.");
