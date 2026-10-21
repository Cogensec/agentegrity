import { test } from "node:test";
import assert from "node:assert/strict";
import { registryVersionDecision } from "./registry_version_policy.mjs";

test("unpublished candidate remains eligible", () => {
  assert.equal(registryVersionDecision({
    publishedCount: 0, packageCount: 7, baselineUnchanged: false,
  }), "unused");
});

test("all seven previously published, unchanged-base PR can run", () => {
  assert.equal(registryVersionDecision({
    publishedCount: 7, packageCount: 7, baselineUnchanged: true,
  }), "published-base");
});

test("release tag never reuses an already published version", () => {
  assert.throws(() => registryVersionDecision({
    publishedCount: 7, packageCount: 7, baselineUnchanged: false,
  }), /collision/);
});

test("PR version rollback or bump into existing version fails", () => {
  assert.throws(() => registryVersionDecision({
    publishedCount: 7, packageCount: 7, baselineUnchanged: false,
  }), /collision/);
});

test("partial npm publication must block even on unchanged baseline", () => {
  for (const publishedCount of [1, 3, 6]) {
    assert.throws(() => registryVersionDecision({
      publishedCount, packageCount: 7, baselineUnchanged: true,
    }), /partial/);
  }
});

test("invalid or incomplete inventory must fail closed", () => {
  for (const args of [
    { publishedCount: -1, packageCount: 7, baselineUnchanged: true },
    { publishedCount: 8, packageCount: 7, baselineUnchanged: true },
    { publishedCount: 7, packageCount: 6, baselineUnchanged: true },
    { publishedCount: undefined, packageCount: 7, baselineUnchanged: true },
  ]) assert.throws(() => registryVersionDecision(args), /invalid/);
});
