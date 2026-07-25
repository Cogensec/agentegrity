# Telemetry

Agentegrity collects anonymous, shape-only usage telemetry to guide development priorities. This page documents every event and property sent, the anonymity mechanism, and how to disable it.

## 1. Why

The framework is a zero-dependency library: there is no server, no signup, no download counter that tells us which parts matter. Telemetry answers two questions we cannot answer any other way: which framework adapters people use (so adapter work goes where the users are), and which evaluation surfaces get exercised (so hardening effort follows real usage). That's it. It is not product analytics, and it carries no content.

## 2. What is collected

Every property is built in one auditable module, [`src/agentegrity/core/_telemetry_props.py`](../src/agentegrity/core/_telemetry_props.py). Payloads carry only counts, booleans, enum values, durations, rounded score floats, and version strings.

| Event | Fired by | Properties |
|---|---|---|
| `client_evaluate_started` | `AgentegrityClient.evaluate` | `risk_tier`, `agent_type`, `deployment_context`, `capability_count` |
| `client_evaluate_finished` | `AgentegrityClient.evaluate` | the started properties, plus per-property scores (`adversarial_coherence`, `environmental_portability`, `verifiable_assurance`, `recovery_integrity`, 3 decimals), `composite`, `composite_bucket`, `passed`, `action`, `duration_ms` |
| `client_attest` | `AgentegrityClient.attest` | `record_kind`, `signed`, `evidence_count` |
| `adapter_created` | `AgentegrityClient.create_adapter` | `adapter` (registry key), `framework_available` — fired even when the framework import fails, so we can see demand for adapters people can't install |
| `monitor_violation` | `IntegrityMonitor` | `action` (log/alert/block/escalate), `property` (lowest-scoring integrity property) |
| `attestation_verified` | `AttestationChain.verify_chain` | `record_count`, `decision_count`, `verified` |
| `cli_run` | `python -m agentegrity` | `command` (`info`, `doctor`, or `verify-decisions`) |
| `agentegrity_uncaught_exception` | outermost telemetry scope | `exception_type` (exception **class name only**) |

Every event also carries coarse environment tags built in [`telemetry.py`](../src/agentegrity/core/telemetry.py): `agentegrity_version`, `python_version` (major.minor), `environment` (`local`/`ci`/`docker`/`colab`/`kaggle`), `os_type` (`sys.platform`), a per-process `$session_id`, and the tags `component`/`operation` naming which API surface fired the event.

## 3. What is never collected

Prompts, model inputs or outputs, tool arguments, file paths, agent or profile names, capability strings, policy text, exception messages or tracebacks, hostnames, usernames, IP-derived identity. Test suites verify this: the leak-guard tests in `tests/test_telemetry.py` assert every shape builder emits only allowlisted keys and no string longer than 64 characters.

## 4. The anonymous ID

A random UUID is stored at `~/.agentegrity/id` (file mode `0600`) on first instrumented use — never on import, and never when telemetry is disabled. If the file cannot be written (read-only home, sandbox), an ephemeral per-process `anon-` ID is used instead. The ID carries no machine or user information; deleting the file resets it.

## 5. How to disable

Any of these fully disables telemetry — no ID file is created, no thread starts, no network is touched:

```bash
export DO_NOT_TRACK=1                       # the cross-tool standard
export AGENTEGRITY_TELEMETRY_DISABLED=1     # agentegrity-specific
```

Truthy values: `1`, `true`, `yes`, `on`, `t`, `y` (case-insensitive). At runtime:

```python
import agentegrity
agentegrity.disable_telemetry()
```

To keep telemetry but disable geographic enrichment on PostHog's side:

```bash
export AGENTEGRITY_TELEMETRY_DISABLE_GEOIP=1
```

## 6. Transport

Events batch through a single daemon thread (stdlib `urllib`, 2-second timeout, bounded queue that drops on overflow) to US-hosted PostHog (`us.i.posthog.com`). Telemetry can never break or block the host process: every failure is swallowed, and offline use simply drops events. The embedded project key is write-only — it can ingest events but never read them back.

---

## Next steps

- **[Quickstart](quickstart.md)** — instrument an agent in three lines
- **[SECURITY.md](../SECURITY.md)** — the project's hardened guarantees, including telemetry auditability
