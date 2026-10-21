/**
 * Decide whether an npm release version may be used by this candidate.
 * Only all-seven fully published, identical-base PRs may keep running after
 * publication. Partial publication and version-reuse attempts fail closed.
 *
 * In the actual tag-triggered release there is no PR base SHA, so this
 * function categorically forbids reusing published versions.
 */
export function registryVersionDecision({ publishedCount, packageCount, baselineUnchanged }) {
  if (!Number.isInteger(publishedCount) || !Number.isInteger(packageCount) ||
      packageCount !== 7 || publishedCount < 0 || publishedCount > packageCount) {
    throw new Error("invalid registry inventory");
  }
  if (publishedCount === 0) return "unused";
  if (publishedCount === packageCount && baselineUnchanged === true) {
    return "published-base";
  }
  throw new Error("registry version collision or partial publication");
}
