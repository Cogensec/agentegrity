#!/usr/bin/env bun
/**
 * Workspace-level TypeScript coverage gate.
 *
 * Bun 1.3.11's bunfig.toml `coverageThreshold` does not actually
 * enforce — `bun test --coverage` exits 0 even with thresholds set
 * far above the measured coverage. So we run `bun test --coverage`,
 * parse the text reporter's "All files" summary line, and exit
 * non-zero if either the line or function coverage is below the
 * configured floor.
 *
 * Run from the workspace root:
 *
 *     cd clients/typescript && bun run scripts/check-coverage.ts
 *
 * Floor is calibrated against the current measured surface
 * (89.99% lines / 83.40% functions across 7 packages, 32 tests).
 * Set conservatively at 80% lines / 70% functions to leave headroom
 * for one stray uncovered branch on a future PR; raise once the
 * weak spots (langchain handler, vercel-ai index) are covered.
 */

import { spawnSync } from "node:child_process";

const MIN_LINE = 80;
const MIN_FUNC = 70;

const result = spawnSync("bun", ["test", "--coverage", "--coverage-reporter=text"], {
  encoding: "utf8",
  stdio: ["ignore", "pipe", "pipe"],
});

if (result.status !== 0 && result.status !== null) {
  console.error("`bun test --coverage` exited non-zero:");
  console.error(result.stdout);
  console.error(result.stderr);
  process.exit(result.status ?? 1);
}

// Combined output — bun writes the coverage table to stdout but we
// also surface stderr in case the test runner decided to use it.
const output = (result.stdout ?? "") + "\n" + (result.stderr ?? "");
process.stdout.write(output);

// Per-file coverage rows look like:
//   packages/client/src/default.ts       |   82.61 |   92.36 | 165,...
//   packages/client/dist/default.js      |   91.30 |   94.78 | ...
// The "All files" aggregate counts both src/ AND dist/, but the
// dist/*.js are tsc-compiled copies of the same source. Counting both
// double-measures the package and drives the aggregate down. We skip
// dist/ rows and recompute the aggregate ourselves so the threshold
// gate measures the actual source surface.
//
// (Bun 1.3.11's bunfig `coveragePathIgnorePatterns` is documented but
// does not actually filter coverage output — verified locally. Worked
// around here.)

interface FileRow {
  path: string;
  funcs: number;
  lines: number;
}

const rowRe =
  /^\s+([A-Za-z][^|\s][^|]*?)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|/gm;
const rows: FileRow[] = [];
for (const m of output.matchAll(rowRe)) {
  const path = (m[1] ?? "").trim();
  if (!path || path === "All files" || path === "File") continue;
  rows.push({
    path,
    funcs: Number(m[2]),
    lines: Number(m[3]),
  });
}

if (rows.length === 0) {
  console.error(
    "\nCould not find any per-file coverage rows in `bun test --coverage` output. " +
      "Has the bun coverage reporter format changed?",
  );
  process.exit(2);
}

// Filter out tsc-compiled dist/ rows — they're a duplicate of the
// corresponding src/ rows and only show up because the cross-package
// parity test imports `@agentegrity/<pkg>` which resolves to dist/.
const srcRows = rows.filter((r) => !r.path.includes("/dist/"));
if (srcRows.length === 0) {
  console.error("\nAfter filtering dist/ rows, no source rows remain.");
  process.exit(2);
}

const funcs = srcRows.reduce((s, r) => s + r.funcs, 0) / srcRows.length;
const lines = srcRows.reduce((s, r) => s + r.lines, 0) / srcRows.length;

let failed = false;
console.log("");
console.log(`Coverage thresholds: lines ≥ ${MIN_LINE}, functions ≥ ${MIN_FUNC}`);
if (lines < MIN_LINE) {
  console.error(`✗ Line coverage ${lines.toFixed(2)}% below floor ${MIN_LINE}%`);
  failed = true;
} else {
  console.log(`✓ Line coverage ${lines.toFixed(2)}% (≥ ${MIN_LINE}%)`);
}
if (funcs < MIN_FUNC) {
  console.error(`✗ Function coverage ${funcs.toFixed(2)}% below floor ${MIN_FUNC}%`);
  failed = true;
} else {
  console.log(`✓ Function coverage ${funcs.toFixed(2)}% (≥ ${MIN_FUNC}%)`);
}

if (failed) {
  console.error("\nTypeScript coverage regressed below the calibrated floor.");
  process.exit(1);
}
console.log("\nTypeScript coverage thresholds met.");
