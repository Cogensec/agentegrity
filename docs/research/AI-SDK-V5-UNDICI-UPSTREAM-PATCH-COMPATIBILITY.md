# AI SDK v5 / Undici security remediation investigation

**Research canary; not an approved dependency override or a release exception.**

Tracking: [Agentegrity issue #97](https://github.com/Cogensec/agentegrity/issues/97).
Upstream request: [Vercel AI issue #23770](https://github.com/vercel/ai/issues/23770).

## Verified published baseline

Our private `clients/typescript/compat/ai-sdk-v5` fixture uses
`ai5: npm:ai@5.0.272` to exercise the advertised v5 integration while
keeping the vulnerable dependencies out of the normal modern workspace.

The v5 dependency ancestry is:

```
ai@5.0.272
  -> @ai-sdk/provider-utils@3.x
  -> undici@5.29.0
  -> @fastify/busboy@2.1.1
```

In isolated test runs the three actual framework instrumentation scenarios
passed on Node 18, 20, and 22, **while npm audit reported five vulnerable
packages (two high, three moderate)**. The baseline tests do not exercise
every `undici` fetch/multipart operation or prove that every consumer can
reach the affected path.

## Nonpublishing vendor candidate comparison

[Workflow](../../.github/workflows/ai-sdk-v5-undici-security-canary.yml)
runs six completely isolated jobs:

| Test resolution | Node majors | Changes |
| --- | --- | --- |
| published-v5 | 18, 20, 22 | normal AI SDK v5 installed graph |
| experimental-undici6 | 18, 20, 22 | `npm overrides.undici=^6.29.1` inside the temporary **private test fixture only** |

Every job builds Agentegrity's shared TypeScript client, runs the actual
v5 instrumentation tests, captures installed dependency ancestry, and
saves live `npm audit` evidence. The experimental job verifies that it
really resolved a 6.x Undici rather than leaving an unnoticed 5.x copy.

**This is an experiment, not a recommendation to consumers to force
undici@6 underneath `@ai-sdk/provider-utils@3`.** An override can
change behavior outside the mock-model tests. Even a completely green
six-job matrix does **not** prove full Undici transport/API compatibility,
multipart safety, fetch/stream behavior, or reachability of every advisory.
A vendor-reviewed backport would require broader integration and
network/multipart behavior tests.

## Vendor coordination and next steps

1. Share the baseline/experimental resolution and audit results on the
   upstream AI SDK v5 maintenance request. Do not claim a safe fix unless
   verified upstream and released to npm.
2. Separately track
   [CrewAI uv update](https://github.com/crewAIInc/crewAI/issues/7633)
   and [ChromaDB stable security patch](https://github.com/chroma-core/chroma/issues/7909).
   CrewAI has already merged an MCP 1.30.0 requirement change but has not
   published a stable release containing it as of October 21, 2026.
3. Keep Agentegrity's hard `optional-dependency-security-gate`
   unchanged. A clean fixture audit would not resolve ChromaDB/CrewAI.

No Tag, TestPyPI/PyPI/npm publication, code release, vulnerability waiver,
or production deployment is authorized by this document.
