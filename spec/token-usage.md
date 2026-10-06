# Token Usage

Adapters report the LLM tokens an agent spends, in one shape for every framework. Usage rides the existing exporter payloads: each `stop` event carries the session's running total in `data.usage`, and the session summary carries the final total in `usage`. Both follow `SessionUsage` in `schemas/exporter/common.json`. When no source reported anything, the field is absent rather than zero.

## What the numbers mean

Frameworks disagree on whether "input tokens" include cached tokens. Anthropic's API leaves them out; OpenAI, Gemini, LangChain and CrewAI count them in. Agentegrity uses the inclusive definition and converts the others:

| Field | Meaning |
|---|---|
| `input_tokens` | Every input token processed, cached or not |
| `cache_read_tokens` | Part of `input_tokens` read from a prompt cache |
| `cache_write_tokens` | Part of `input_tokens` written to a prompt cache |
| `output_tokens` | Every generated token, reasoning included |
| `reasoning_tokens` | Part of `output_tokens` spent on reasoning |
| `total_tokens` | `input_tokens + output_tokens` |
| `requests` | Model calls counted |

The cache, reasoning and request fields are absent when no source reported them. Anthropic does not break out thinking tokens per request, so a Claude session has no `reasoning_tokens` even when the model reasoned; those tokens are inside `output_tokens`.

Totals are also kept per model in `by_model`, so a session that switches models, or runs subagents on a smaller one, shows each separately.

## Provenance

`sources` lists where the counts came from:

- `transcript`: a coding-agent host's own session log.
- `provider_response`: the usage object on the framework's model response.
- `trace`: a framework trace or telemetry event.

`complete` is `false` when calls are known to be missing, so the totals are a lower bound. Causes:

- The transcript starts after a compaction that dropped earlier history.
- A request never got its final usage line. Claude Code sometimes omits it for subagent requests.
- The counts were reconstructed from running totals. Codex builds without `token_usage_record` lines report usage only as running totals.

## Deduplication

Every entry is keyed by the model call it counts (a request id, a response id, a run id). Recording a key again replaces the earlier entry. That is how sources that repeat a call are counted once:

- **Claude Code transcripts** write one line per content block, each with the request's usage.
- **Running totals** are reported again after every call, so each new report replaces the previous one under the same key.

Adapters call `record_usage(key, model, TokenUsage(...), source=...)`; a missing model falls back to the profile's `model_id`.

## Hook daemon restarts

Each hook daemon lifetime is its own exporter session. The runtime saves how far each transcript was read in `<hook dir>/<host>/<session>.usage.json` (mode 0600), so a restarted daemon reports only the tokens spent after the previous one stopped, and adding up a conversation's sessions gives its total. The file holds paths, read positions, a hash of each file's first line, and the id of the last call counted. It holds no token counts and no transcript content.

A transcript that was replaced while no daemon ran (a cloud session resuming from a transcript rebuilt after compaction) is detected by its first line, since a replacement can reuse the inode. Lines up to the last call already counted are skipped; a replacement that does not contain that call is counted whole.

## Sources per integration

| Integration | Source | Notes |
|---|---|---|
| Claude Code | `transcript` | Reads `transcript_path` and each subagent's `agent_transcript_path` at every `Stop` and at session end, only the lines added since the last read. Keyed by `requestId`; the last line of a request wins. |
| Codex | `transcript` | Reads `token_usage_record` lines from the rollout, keyed by `response_id`; model from `turn_context` or the hook's `model`. Older builds fall back to `token_count` events, marked incomplete. |
| Claude Agent SDK (Python) | `transcript`, or `provider_response` with `observe()` | Hooks name the transcript, so usage needs no code change. Passing each message to `adapter.observe(message)` replaces the estimate with `ResultMessage.model_usage`, which includes subagents. |
| Other Python adapters, TypeScript packages | not yet | Planned. |

Transcript readers keep only usage counts, request ids and model names. Nothing else from a transcript is stored or exported.
