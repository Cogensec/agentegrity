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
| OpenAI Agents SDK | `provider_response` | `on_llm_end` counts each `response.usage`, keyed by `response_id`, with the agent's model. An agent run as a tool shares its parent's `Usage` object but fires no hooks, so what the shared total holds beyond the counted calls is recorded with model `unknown`. |
| LangChain / LangGraph | `provider_response` | `on_llm_end` reads each generation's `usage_metadata`, keyed by `run_id` so a handler attached twice counts once. Anthropic's split cache-write keys and OpenAI's tier-prefixed keys (`priority_cache_read`) are included. Model from `response_metadata`, else the chat model's start metadata. |
| CrewAI | `provider_response` | `LLMCallCompletedEvent` per `call_id`, normalized with CrewAI's `UsageMetrics.from_provider_dict`. Not `crew.usage_metrics`, which counts an LLM shared by several agents once per agent. Releases without the event report nothing. |
| AutoGen | `provider_response` | `LLMCallEvent` / `LLMStreamEndEvent` from the `autogen_core.events` logger. The adapter lowers that logger to INFO behind one shared filter that records usage and passes on only the records the logger emitted before, so log output does not change; the level is restored when the last adapter closes. Anthropic cache counts are added to input. |
| Agno | `provider_response` | `run_output.metrics` in the post-hook, per model from `metrics.details`. Re-read at every stop and at close, because Agno merges memory and summary model calls into the metrics after post-hooks run. Anthropic, Bedrock and Vertex Claude report input without the cache, so it is added; Gemini reports thinking outside output, so it is added there. |
| Google ADK | `provider_response` | `after_model_callback`, final responses only (streamed partials repeat the usage). Prompt counts include the cache; thinking tokens are added to output and tool-use prompt tokens to input. |
| Bedrock Agents (boto3) | `trace` | `modelInvocationOutput.metadata.usage` from orchestration, pre/post-processing and routing traces, keyed by `traceId`. No cache fields. Most traces do not name the model, so it falls back to the profile's `model_id`. |
| Bedrock Agents (Strands) | `provider_response` | The invocation's usage from `AfterInvocationEvent`. Cache counts are added to input unless input + output already equals the total, the rule Strands itself uses. |
| TypeScript packages | not yet | Planned. |

Transcript readers keep only usage counts, request ids and model names. Nothing else from a transcript is stored or exported.
