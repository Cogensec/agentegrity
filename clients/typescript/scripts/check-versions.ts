#!/usr/bin/env node
/**
 * Check release parity for every published @agentegrity/* package, its pinned
 * internal client dependency, and Bun's frozen workspace lockfile.
 *
 * Run in CI before tagging. The private @agentegrity/workspace root has its
 * own non-release version and is deliberately excluded.
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
const pkgsDir = resolve(new URL("../packages", import.meta.url).pathname);
const pkgDirs = readdirSync(pkgsDir);
const workspaceRoot = resolve(new URL("..", import.meta.url).pathname);

let failed = false;
let published = 0;
let dependentPackages = 0;
for (const name of pkgDirs) {
  const pkgPath = join(pkgsDir, name, "package.json");
  try {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf8")) as {
      name: string;
      version: string;
      private?: boolean;
      dependencies?: Record<string, string>;
    };
    if (pkg.private) continue;
    published++;
    if (pkg.version !== pyVersion) {
      console.error(`✗ ${pkg.name}@${pkg.version} != pyproject ${pyVersion}`);
      failed = true;
    } else {
      console.log(`✓ ${pkg.name}@${pkg.version}`);
    }

    if (pkg.name !== "@agentegrity/client") {
      dependentPackages++;
      const dependency = pkg.dependencies?.["@agentegrity/client"];
      if (dependency !== pyVersion) {
        console.error(
          `✗ ${pkg.name} must pin @agentegrity/client@${pyVersion} (found ${dependency})`,
        );
        failed = true;
      }
    }
  } catch (err) {
    console.error(`✗ cannot validate ${pkgPath}: ${String(err)}`);
    failed = true;
  }
}

// Bun's JSONC-style lockfile has trailing commas. Inspect only the workspace
// metadata, not external package versions in the full dependency resolution.
const lock = readFileSync(join(workspaceRoot, "bun.lock"), "utf8");
const start = lock.indexOf('  "workspaces": {');
const end = lock.indexOf('\n  "packages": {', start);
if (start < 0 || end < 0) {
  console.error("✗ bun.lock has no recognizable workspace metadata");
  failed = true;
} else {
  const workspaces = lock.slice(start, end);
  const versions = [...workspaces.matchAll(/^\s*"version": "([^"]+)"/gm)]
    .map((match) => match[1]);
  const clientPins = [...workspaces.matchAll(/^\s*"@agentegrity\/client": "([^"]+)"/gm)]
    .map((match) => match[1]);

  if (versions.length !== published || versions.some((v) => v !== pyVersion)) {
    console.error(
      `✗ bun.lock workspace versions ${JSON.stringify(versions)} do not match ${published} packages at ${pyVersion}`,
    );
    failed = true;
  }
  if (clientPins.length !== dependentPackages || clientPins.some((v) => v !== pyVersion)) {
    console.error(
      `✗ bun.lock internal client pins ${JSON.stringify(clientPins)} do not match ${dependentPackages} packages at ${pyVersion}`,
    );
    failed = true;
  }
  if (!failed) {
    console.log(`✓ bun.lock ${published} workspace versions / ${dependentPackages} client pins`);
  }
}

if (failed) {
  console.error("\nVersion drift detected. Bump all published packages and the frozen lockfile together.");
  process.exit(1);
}
console.log(`\nAll published packages and Bun workspace entries match pyproject ${pyVersion}.`);
