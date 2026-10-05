#!/usr/bin/env node
/**
 * Assert that every `@agentegrity/*` package.json agrees with the release:
 * its version and its `@agentegrity/client` pin match the Python
 * `pyproject.toml` version, and its `repository.url` matches the repository
 * npm provenance will attest. Run in CI and before publishing a release.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";

const repoRoot = resolve(new URL("../../..", import.meta.url).pathname);
const pyproject = readFileSync(join(repoRoot, "pyproject.toml"), "utf8");
const pyMatch = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
if (!pyMatch) {
  console.error("could not find version in pyproject.toml");
  process.exit(2);
}
const pyVersion = pyMatch[1];

// Set by GitHub Actions with the repository's exact casing. npm compares
// repository.url against the provenance claim case-sensitively, so a URL that
// differs only in case fails `npm publish` under trusted publishing.
const actionsRepo = process.env.GITHUB_REPOSITORY;

interface Pkg {
  name: string;
  version: string;
  private?: boolean;
  repository?: { url?: string };
  dependencies?: Record<string, string>;
}

function problems(pkg: Pkg): string[] {
  const found: string[] = [];
  if (pkg.version !== pyVersion) {
    found.push(`${pkg.name}@${pkg.version} does not match pyproject ${pyVersion}`);
  }
  const pin = pkg.dependencies?.["@agentegrity/client"];
  if (pin !== undefined && pin !== pyVersion) {
    found.push(`${pkg.name} pins @agentegrity/client@${pin}, not ${pyVersion}`);
  }
  const url = (pkg.repository?.url ?? "").replace(/^git\+/, "").replace(/\.git$/, "");
  if (!url) {
    found.push(`${pkg.name} has no repository.url, which npm provenance requires`);
  } else if (actionsRepo) {
    const expected = `https://github.com/${actionsRepo}`;
    if (url !== expected && url.toLowerCase() === expected.toLowerCase()) {
      found.push(`${pkg.name} repository.url ${url} differs from ${expected} only in case`);
    }
  }
  return found;
}

const pkgsDir = resolve(new URL("../packages", import.meta.url).pathname);
const pkgs = readdirSync(pkgsDir);

let failed = false;
for (const name of pkgs) {
  const pkgPath = join(pkgsDir, name, "package.json");
  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8")) as Pkg;
    if (pkg.private) continue;
    const found = problems(pkg);
    for (const problem of found) console.error(`✗ ${problem}`);
    if (found.length) {
      failed = true;
    } else {
      console.log(`✓ ${pkg.name}@${pkg.version}`);
    }
  } catch {
    // ignore
  }
}

if (failed) {
  console.error("\nRelease metadata drift detected. Fix every package together.");
  process.exit(1);
}
console.log(`\nAll packages match pyproject version ${pyVersion}.`);
