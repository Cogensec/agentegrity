# agentegrity for Claude Code

Stateful integrity verification for every tool call in a Claude Code
session, with a hash-linked decision chain you can verify afterwards.
Everything runs locally. Sessions stream to an
[agentegrity-pro](https://app.cogensec.com) console only when you set
`AGENTEGRITY_TOKEN` and `AGENTEGRITY_EXPORTER_URL`.

## What it does

The plugin registers `agentegrity hook --host claude-code` on every
session event. The first hook starts a small daemon for the session,
so the layers see the whole conversation in order, not one call at a
time:

- **Argument classification.** Shell commands are tokenized like the
  shell sees them and tagged: a downloaded or decoded payload piped
  into an interpreter, or deleting logs and shell history, is
  **denied**; `$IFS` splicing and computed executables **ask**.
- **Sensitive-data egress (GOV-005).** An external send after a secret
  was read anywhere in the session (or in the same command) **asks**.
  Reading a secret alone is allowed and recorded.
- **Output scanning.** Injection in anything a tool returns lowers the
  score until the context is compacted.
- **Written content.** Instructions written into files (`Write`,
  `Edit`), the way an injection persists across sessions, **ask**.
- **Token usage.** Read from the session transcript and each subagent's,
  normalized and totalled per model, and sent on every turn and at session
  end. Only usage counts and model names are kept from the transcript.
- **Behavioral drift.** Each clean session teaches a per-agent baseline
  (`~/.agentegrity/claude-code/baselines/`); later sessions whose tool mix
  departs from it lower the score and alert. Drift never blocks here.
- **Governance.** Sensitive tool names are gated, MCP-aware:
  `file_delete` also gates `mcp__filesystem__file_delete`.
- **Decision chain.** Every evaluation and every non-allow verdict is
  appended to `~/.agentegrity/claude-code/<session>.chain.json`.
  Verify it with:

  ```bash
  agentegrity verify-decisions ~/.agentegrity/claude-code/<session>.chain.json
  ```

## Install

```bash
pip install "agentegrity>=0.11.0"   # must be importable by the python3 on PATH
```

The plugin version matches the library version it needs. If the hook cannot run (library
missing, or older than 0.11.0), the plugin shows a warning and the action goes through
unchecked instead of being blocked.

Then, inside Claude Code:

```
/plugin marketplace add cogensec/agentegrity
/plugin install agentegrity@agentegrity
```

Check the wiring with `/agentegrity-status`.

## Configuration

Environment variables, all optional, shared with the Codex plugin:

| Variable | Default | Effect |
|---|---|---|
| `AGENTEGRITY_HOOK_MODE` | `enforce` | `alert` records verdicts but never blocks or asks |
| `AGENTEGRITY_HOOK_DISABLED` | unset | `1` disables the hook entirely |
| `AGENTEGRITY_RISK_TIER` | `high` | Profile risk tier for governance gating |
| `AGENTEGRITY_HOOK_DIR` | `~/.agentegrity` | Root for `<host>/<session>.chain.json` |
| `AGENTEGRITY_HOOK_IDLE_SECONDS` | `1800` | Daemon exits after this long without a hook |
| `AGENTEGRITY_AGENT_ID` | `claude-code` | Agent the console groups this host's sessions under |
| `AGENTEGRITY_AGENT_NAME` | `claude-code` | Display name the console shows for that agent |
| `AGENTEGRITY_MODEL_ID` | unset | Model reported on the agent profile (the configured model; a mid-session model switch is not tracked) |
| `AGENTEGRITY_TOKEN`, `AGENTEGRITY_EXPORTER_URL` | unset | Stream sessions to a console |

## Failure semantics

The hook never breaks a session. If the daemon cannot run (no Unix
sockets, or the runtime directory is not private to your user), each
call is evaluated in-process from the persisted chain: single-call rules
still enforce, but cross-call rules and streaming are lost. If the
daemon is up but does not answer in time, or `agentegrity` is not
importable, the hook stays silent and Claude Code's normal permission
flow decides. In `alert` mode the chain records what the verdict would
have been.

## Migrating from 0.1

The per-call `hooks/pretooluse.py` is replaced by the shared runtime.

- Environment variables were renamed: `AGENTEGRITY_CC_MODE` is
  `AGENTEGRITY_HOOK_MODE`, `AGENTEGRITY_CC_DISABLED` is
  `AGENTEGRITY_HOOK_DISABLED`, `AGENTEGRITY_CC_RISK_TIER` is
  `AGENTEGRITY_RISK_TIER`, and `AGENTEGRITY_CC_CHAIN_DIR` is replaced by
  `AGENTEGRITY_HOOK_DIR` (chains now live under `<dir>/claude-code`).
  **The old names are ignored**, so a hook you disabled with
  `AGENTEGRITY_CC_DISABLED=1` is active again until you rename it.
- Commands are no longer text-scanned. A bare `cat ~/.aws/credentials`
  used to be denied; it is now allowed and recorded, and sending the
  data out afterwards asks.
- Existing chain files are resumed, not replaced.

## What this is not

The hook evaluates tool calls, not the model's reasoning. Static
command analysis is a floor: anything the shell computes at runtime is
flagged as obfuscated, not resolved. Treat it as a measurement and
enforcement seam with verifiable provenance, not a guarantee.
