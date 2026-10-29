# Universal host agent observation (U1/U4)

**Status:** Opt-in, host-side metadata recorder built on the canonical agent
event v1 contract from [PR #110](https://github.com/Cogensec/agentegrity/pull/110).
**Dependency:** PR #110 must merge before this PR. The implementation
does not modify legacy exporter event emission or claim any native agent
harness support.

The `HostAgentObserver` records explicit application-host lifecycle and
tool-call metadata through the canonical contract. It works when the
customer owns the tool dispatch boundary, whether the application hosts a
financial agent, SaaS workflow, customer-support agent, browser agent, custom
research agent, infrastructure agent, or a physical simulation.

## API

```python
from agentegrity import HostAgentObserver, ToolCompletion

observer = HostAgentObserver(
    agent_id="research-agent", run_id="run-17",
    host="my-host", integration="host-sdk",
)
observer.start()
observer.tool_intent(action_id="call-17", tool_name="search_index", effect="read")
observer.tool_outcome(action_id="call-17", completion=ToolCompletion.RETURNED)
observer.finish()
events = observer.export()  # metadata only; no automatic network export
```

**Trusted host input:** The application supplies the agent and run identity,
lifecycle observations, tool names, and effect types. Do not construct
these from agent text or an untrusted tool response. The
`host_asserted` assurance label is **not independently authenticated**.
Host-controlled tool returns have outcome `returned_unverified`;
timeouts, errors and cancellations remain uncertain about external effects.

**No content collection:** This API has no arguments for prompts, raw
tool inputs/outputs, retrieved pages, customer data, credentials or
arbitrary metadata. The bounded recorder raises `EventCapacityExceeded`
rather than silently losing events, reserves room for pending outcomes and
a final run event, and rejects reused action IDs within a run. Closing
a run records each unresolved call as `abandoned_uncertain` without
claiming effect failure or rollback.

## Tests

```bash
python -m pytest tests/test_host_agent_observer.py -v
python examples/host_agent_observation.py
```

Tests exercise nine simulated agent categories, capacity exhaustion,
out-of-order calls, duplicate action IDs, timestamp validation,
crash/abandonment semantics, and concurrent host callbacks. These tests
do not validate host SDK interception, LLM behavior, detection rates, or
remote attestation.

## Separation of responsibilities

- **Observe:** This class captures canonical metadata from explicit calls.
- **Evaluate:** Attach actual `IntegrityEvaluator` integration in a
  separate implementation; this recorder does not compute scores.
- **Authorize:** Use an independently installed execution-boundary
  `ActionSecurityGate` or `TrustedActionGateway` in the real tool path.
- **Verify:** External effects require trusted downstream evidence.
- **Recover:** Agent restarts and observer close do not restore business state.

A future host integration must provide actual lifecycle callbacks, test
whether any tool denial is effective, and publish a truthful host-versioned
capability card. This generic recorder alone does not imply complete
support for a named agent or agent category.
