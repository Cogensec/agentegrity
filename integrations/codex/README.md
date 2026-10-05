# agentegrity for Codex

Stateful integrity verification for every tool call in a Codex session,
with a hash-linked decision chain you can verify afterwards. It runs on
Codex's native hooks and shares its runtime with the Claude Code plugin,
so the rules and the chain format are identical.

## What it does

The plugin registers `agentegrity hook --host codex` on `PreToolUse`,
`PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStart`,
`SubagentStop`, `PreCompact` and `SessionEnd`. The first hook starts a
daemon for the session, so the layers see the whole conversation:

- **Argument classification.** A downloaded or decoded payload piped
  into an interpreter, or deleting logs and shell history, is
  **denied**. `apply_patch` is read as a file edit, never as shell:
  its paths come from the `*** Update/Add/Delete File:` headers.
- **Sensitive-data egress (GOV-005).** An external send after a secret
  was read in the session (or in the same command) is **denied**.
  Reading a secret alone is allowed and recorded.
- **Output scanning and failures.** Injection in tool output lowers the
  score until compaction. A non-zero `tool_response.exit_code` is
  recorded as a failure (Codex has no separate failure event).
- **Written content.** Instructions added to files by `apply_patch`
  are **denied**.
- **Decision chain** at `~/.agentegrity/codex/<session>.chain.json`:

  ```bash
  agentegrity verify-decisions ~/.agentegrity/codex/<session>.chain.json
  ```

Codex enforces only `deny` (it parses `ask` but does not prompt), so
anything that would ask on Claude Code is denied here, with the reason
shown.

## Install

```bash
pip install "agentegrity>=0.11.0"   # must be importable by python3 on PATH
codex plugin marketplace add cogensec/agentegrity
```

Install `agentegrity` from the marketplace, then run `/hooks` once to
review and trust the plugin's hooks: Codex does not run non-managed
hooks until you do. Organizations can ship them as managed hooks
through `requirements.toml` instead.

The plugin version matches the library version it needs. If the hook cannot run (library
missing, or older than 0.11.0), the plugin shows a warning and the action goes through
unchecked instead of being blocked.

## Configuration

Same environment variables as the Claude Code plugin:
`AGENTEGRITY_HOOK_MODE` (`enforce` or `alert`),
`AGENTEGRITY_HOOK_DISABLED`, `AGENTEGRITY_RISK_TIER`,
`AGENTEGRITY_HOOK_DIR`, `AGENTEGRITY_HOOK_IDLE_SECONDS`,
`AGENTEGRITY_AGENT_ID` and `AGENTEGRITY_AGENT_NAME` (the agent the
console groups sessions under and its display name; both default to
`codex`), and
`AGENTEGRITY_TOKEN` with `AGENTEGRITY_EXPORTER_URL` to stream sessions
to a console.

## Limits

These come from Codex's hook coverage and cannot be closed from a hook:

- `write_stdin` has no `PreToolUse`, so input typed into an already
  running shell is not checked.
- A command still running when its call returns gets no `PostToolUse`;
  its output is never scanned.
- Hosted tools (web search) do not go through hooks.
- On Windows there is no daemon: each call is evaluated in-process from
  the persisted chain, so single-call rules enforce but cross-call
  rules (GOV-005 across calls) and streaming do not. Use `python`
  instead of `python3` in `hooks/hooks.json` if that is your launcher.
