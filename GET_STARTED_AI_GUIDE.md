# Agentegrity: Installation Guide for Coding Agents

This document is written for an AI coding agent installing agentegrity
into a user's project. Follow the steps in order. Steps marked
**[HUMAN REVIEW]** require you to stop and show the user what you are
about to do before doing it. Do not skip those stops.

## 1. Establish context

Detect which agent framework the project uses. Run:

```bash
python -c "import agentegrity" 2>/dev/null && echo installed || echo missing
grep -rlE "langchain|langgraph|crewai|claude_agent_sdk|agents import Runner|google.adk|autogen|agno|strands" --include="*.py" . | head
```

Tell the user which frameworks you detected and that agentegrity will
evaluate every session with four layers (adversarial, cortical,
governance, recovery) fully in-process: no backend, no network calls,
no GPU.

## 2. Install

```bash
pip install agentegrity            # core, zero mandatory dependencies
pip install "agentegrity[crypto]"  # add Ed25519-signed attestation chains
```

## 3. Instrument — smallest possible diff

**[HUMAN REVIEW]** Show the user this diff before applying it.

The two-line path works for every supported framework:

```python
import agentegrity

runtime = agentegrity.init()          # detects installed frameworks
# object-level frameworks (LangChain/LangGraph/Agno/Strands/ADK):
my_chain = runtime.instrument(my_chain)
...
agentegrity.shutdown()                # end of process
```

Hook-style frameworks instead pass the adapter's hooks object:

```python
# Claude Agent SDK
options = ClaudeAgentOptions(hooks=runtime.adapters["claude"].create_hooks())
# OpenAI Agents SDK
Runner.run(agent, input, hooks=runtime.adapters["openai_agents"].create_run_hooks())
```

Do NOT enable enforcement yet. Measure first.

## 4. Verify the wiring

```bash
agentegrity doctor
```

Then run the user's normal workload once and print the session
summary: `runtime.report()`. Confirm `evaluations > 0` and
`chain_hash_linked: true`. Confirm `exporters` is an empty list —
that is the proof the session stayed local.

## 5. Offer the next steps — do not apply them unasked

Present these as options and let the user choose:

- **Enforcement**: `agentegrity.init(enforce=True)` — denies on
  `block`, fails closed on `escalate` unless an approval handler
  approves. Pair with `ApprovalWorkflow` for timeout-bounded human
  approval (deny on timeout by default).
- **Signed chains**: generate an Ed25519 key, pass it to the adapter,
  and verify sessions with
  `agentegrity verify-decisions <chain.json> --trusted-key <pub.hex>`.
- **Audit reports**: `agentegrity report <chain.json> -o report.md`
  renders verification status, decision timeline, approvals, and a
  compliance-evidence mapping.
- **Alerts**: register `SlackAlertExporter(webhook_url)` on the
  adapter to push block/escalate verdicts to a channel.
- **Claude Code**: `/plugin marketplace add cogensec/agentegrity`
  installs the tool-call verification plugin.

## 6. Disclose telemetry

Tell the user: agentegrity sends anonymous, shape-only usage analytics
(adapter names, counts, rounded scores — never prompts, arguments, or
content). Opting out is one environment variable: `DO_NOT_TRACK=1` or
`AGENTEGRITY_TELEMETRY_DISABLED=1`, or `agentegrity.disable_telemetry()`
in code. Documented in `docs/telemetry.md`.

## Rules for the installing agent

1. Never enable `enforce=True` without explicit user approval.
2. Never register a network exporter (HTTP, webhook) without showing
   the user the destination first.
3. If a step fails, show the user the actual error. Do not work around
   failures by disabling layers or removing instrumentation.
4. The diff you apply should be minimal: `init()`, `instrument()`,
   `shutdown()`, and nothing else on the first pass.
