# agentegrity for Claude Code

In-process integrity verification for every tool call in a Claude Code
session. No backend, no network round-trip, no GPU: the full evaluation
runs locally in the hook, typically in single-digit milliseconds after
interpreter startup.

## What it does

Every tool call passes through a `PreToolUse` hook before it executes:

- The **adversarial layer** scans the tool arguments for injection and
  exfiltration shapes (32-pattern taxonomy, minus the structure-cue
  patterns that would false-positive on ordinary code).
- The **governance layer** gates sensitive tool names, MCP-aware:
  `file_delete` in the sensitive set also gates
  `mcp__filesystem__file_delete`, and glob entries like `mcp__db__*`
  gate a whole server.
- The verdict maps to Claude Code's permission flow: **allow** (hook
  stays silent), **ask** (inline approval with the reason), or
  **deny** (blocked before the tool runs).
- Every evaluated call appends a hash-linked `DecisionRecord` to a
  per-session chain, so the session itself becomes verifiable
  evidence:

  ```bash
  agentegrity verify-decisions ~/.agentegrity/claude-code/<session>.chain.json
  ```

## Install

```bash
pip install agentegrity          # the hook imports it at evaluation time
```

Then, inside Claude Code:

```
/plugin marketplace add cogensec/agentegrity
/plugin install agentegrity@agentegrity
```

Check the wiring with `/agentegrity-status`.

## Configuration

Environment variables, all optional:

| Variable | Default | Effect |
|---|---|---|
| `AGENTEGRITY_CC_MODE` | `enforce` | `alert` records verdicts but never blocks or asks |
| `AGENTEGRITY_CC_DISABLED` | unset | `1` disables the hook entirely |
| `AGENTEGRITY_CC_RISK_TIER` | `high` | Profile risk tier for governance gating |
| `AGENTEGRITY_CC_CHAIN_DIR` | `~/.agentegrity/claude-code` | Decision-chain directory |

## Failure semantics

Fail-open by design: if `agentegrity` is not importable for the
`python3` on PATH, the payload is malformed, or chain persistence
fails, the hook stays silent and Claude Code's normal permission flow
decides. A fail-open event is logged to stderr, and
`/agentegrity-status` reports it. The decision chain records what the
verdict *would have been* in `alert` mode, so a dry run produces the
same auditable evidence as enforcement.

## What this is not

The hook evaluates tool *calls*, not the model's reasoning, and its
regex tier only catches the attack shapes it knows (see STATUS.md for
published benchmark numbers, including the weak ones). Treat it as a
measurement and enforcement seam with verifiable provenance, not a
guarantee.
