# CrewAI patched dependency compatibility investigation

**Status: experiment, not a production-safe dependency upgrade.**

Tracking issue: [Agentegrity #97](https://github.com/Cogensec/agentegrity/issues/97).
Release of v0.13.0 remains blocked by its established security gate.

## Why this test exists

As checked on October 20–21, 2026, the latest published stable CrewAI
release is **1.15.28**. Its published Python dependency graph constrains
`mcp~=1.28.1`, `chromadb~=1.5.9` and through
`crewai-cli==1.15.28`, `uv~=0.11.6`.

Security scanning flagged MCP 1.28.1 (streamable HTTP session resource
exhaustion) with fixes available in **MCP 1.30.0 / 2.2.0**, and uv 0.11.x
(vendored TLS root set) fixed at **uv 0.13.4**. The latest stable
ChromaDB 1.5.9 remains flagged for four distinct security issues without
a patched version published in the advisory database.

CrewAI's main development branch has already widened the MCP range to
`mcp>=1.30.0,<2`, but that is **not** evidence of a stable published
package containing the change. Its CLI still pins the earlier uv series.

Relevant primary upstream references:
- [CrewAI current source metadata](https://github.com/crewAIInc/crewAI/blob/main/lib/crewai/pyproject.toml)
- [CrewAI CLI current source metadata](https://github.com/crewAIInc/crewAI/blob/main/lib/cli/pyproject.toml)
- [Chroma stable release advisory investigation](https://github.com/chroma-core/chroma/issues/7909)
- [MCP resource exhaustion advisory](https://github.com/advisories/GHSA-84m7-p3x7-pcfv)

## How to interpret the canary

A dedicated, **nonpublishing** GitHub Actions workflow installs the
unchanged published CrewAI integration and compares these two scenarios
using isolated, ephemeral GitHub-hosted runners:

| Scenario | Installed CrewAI | MCP | uv |
| --- | --- | --- | --- |
| Published baseline | 1.15.28 | resolver-selected, `~=1.28.1` | resolver-selected, `~=0.11.6` |
| Patched-runtime experiment | 1.15.28 | force-installed 1.30.0 | force-installed 0.13.4 |

The patched experiment *deliberately overrides* CrewAI's published
requirements **only inside CI**. `pip check` output is saved as evidence
and will likely show dependency conflicts. The canary then performs
credential-free imports and an Agentegrity-CrewAI event-bus subscription,
an MCP HTTP transport/configuration initialization, and a `uv --version`
check. It never connects to a model provider, launches a server, or uses
production credentials.

**Passing does not prove upstream compatibility**: it does not exercise
every CrewAI feature, RAG backend, MCP remote server, or CLI command,
and it cannot prove that a resolver will install the candidate pins in a
supported environment. It only offers an initial regression signal that
can inform upstream-compatible maintenance.

## Recommended next steps

1. Review both canary runs and their `pip-check.txt` artifacts.
2. Ask CrewAI maintainers to release a version widening its **published**
   MCP bound, and separately review the uv dependency floor; include
   actionable reproduction evidence rather than untested forced overrides.
3. Wait for a published, fixed ChromaDB release and a compatible CrewAI
   dependency bump, or choose a fundamentally different, reviewed
   dependency architecture if the vendor cannot address its advisories.
4. Re-run the real installed-tree `pip-audit` and isolated legacy AI SDK v5
   audit. The latter still requires a vendor-compatible Undici/Busboy
   remedy or an explicit, independently reviewed support decision.
5. Keep v0.13.0 **unpublished** until the full release security gate
   passes or a documented, authorized exception is reviewed.

Do not replace Agentegrity's supported dependency metadata with the
ephemeral canary's versions and do not infer `pip check` success from
passing runtime imports.
