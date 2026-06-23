"""Live test for AutoGenAdapter.

The conformance suite (``test_adapter_conformance.py``) drives
``on_event`` directly and proves the base-class contract holds.
This module exercises the framework-specific glue: that real
OpenTelemetry spans emitted via autogen's GenAI tracing helpers
flow through our ``SpanProcessor`` and produce the expected
canonical events.

Skipped when ``opentelemetry-sdk`` is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode

from agentegrity.adapters.autogen import AutoGenAdapter
from agentegrity.core.profile import AgentProfile

# Mirror the GenAI semconv keys the adapter watches for. AutoGen's
# trace helpers set the same strings (autogen_core._telemetry._genai).
_GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
_GEN_AI_AGENT_NAME = "gen_ai.agent.name"
_GEN_AI_AGENT_ID = "gen_ai.agent.id"
_GEN_AI_TOOL_NAME = "gen_ai.tool.name"


def _profile() -> AgentProfile:
    return AgentProfile.default()


def _build_adapter() -> tuple[AutoGenAdapter, TracerProvider]:
    """Create an adapter with an isolated TracerProvider.

    We deliberately do NOT call ``adapter.instrument(set_global=True)``
    here so concurrent tests don't clobber each other's global state.
    """
    adapter = AutoGenAdapter(profile=_profile())
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(adapter.span_processor())
    return adapter, tracer_provider


def test_root_invoke_agent_emits_user_prompt_submit_and_stop() -> None:
    adapter, tracer_provider = _build_adapter()
    tracer = tracer_provider.get_tracer("autogen-core")
    with tracer.start_as_current_span(
        "invoke_agent root",
        attributes={
            _GEN_AI_OPERATION_NAME: "invoke_agent",
            _GEN_AI_AGENT_NAME: "root_agent",
        },
    ):
        pass

    event_types = [e.event_type for e in adapter.events]
    # The root invoke_agent span seeds a GROUP_CHAT topology, so
    # topology_declared leads the stream.
    assert event_types == ["topology_declared", "user_prompt_submit", "stop"]
    assert adapter.evaluation_count == 3  # topology_declared + both lifecycle events
    assert adapter.attestation_chain.verify_chain()


def test_nested_invoke_agent_emits_subagent_events() -> None:
    adapter, tracer_provider = _build_adapter()
    tracer = tracer_provider.get_tracer("autogen-core")
    with tracer.start_as_current_span(
        "invoke_agent root",
        attributes={
            _GEN_AI_OPERATION_NAME: "invoke_agent",
            _GEN_AI_AGENT_NAME: "root_agent",
        },
    ):
        with tracer.start_as_current_span(
            "invoke_agent child",
            attributes={
                _GEN_AI_OPERATION_NAME: "invoke_agent",
                _GEN_AI_AGENT_NAME: "child_agent",
                _GEN_AI_AGENT_ID: "child-id-42",
            },
        ):
            pass

    event_types = [e.event_type for e in adapter.events]
    # Root span seeds the GROUP_CHAT topology (topology_declared); the
    # nested child invoke_agent grows it by one member (topology_change).
    assert event_types == [
        "topology_declared",
        "user_prompt_submit",
        "topology_change",
        "subagent_start",
        "subagent_stop",
        "stop",
    ]
    # subagent_start data carries the agent id when available.
    subagent_start = next(e for e in adapter.events if e.event_type == "subagent_start")
    assert subagent_start.data["agent_id"] == "child-id-42"


def test_execute_tool_success_maps_to_pre_and_post_tool_use() -> None:
    adapter, tracer_provider = _build_adapter()
    tracer = tracer_provider.get_tracer("autogen-core")
    with tracer.start_as_current_span(
        "execute_tool search",
        attributes={
            _GEN_AI_OPERATION_NAME: "execute_tool",
            _GEN_AI_TOOL_NAME: "search",
        },
    ):
        pass

    event_types = [e.event_type for e in adapter.events]
    assert event_types == ["pre_tool_use", "post_tool_use"]
    pre = next(e for e in adapter.events if e.event_type == "pre_tool_use")
    assert pre.data["tool_name"] == "search"


def test_execute_tool_failure_maps_to_post_tool_use_failure() -> None:
    adapter, tracer_provider = _build_adapter()
    tracer = tracer_provider.get_tracer("autogen-core")
    span_cm = tracer.start_as_current_span(
        "execute_tool broken",
        attributes={
            _GEN_AI_OPERATION_NAME: "execute_tool",
            _GEN_AI_TOOL_NAME: "broken",
            "error.type": "ValueError",
        },
    )
    with span_cm as span:
        span.set_status(Status(StatusCode.ERROR, "tool failed"))

    event_types = [e.event_type for e in adapter.events]
    assert event_types == ["pre_tool_use", "post_tool_use_failure"]
    failure = adapter.events[-1]
    assert failure.data["tool_name"] == "broken"
    assert failure.data["error"] == "ValueError"


def test_create_agent_span_is_ignored() -> None:
    """create_agent spans fire during agent construction; we ignore them
    because no canonical event maps cleanly to "an agent was created"."""
    adapter, tracer_provider = _build_adapter()
    tracer = tracer_provider.get_tracer("autogen-core")
    with tracer.start_as_current_span(
        "create_agent worker",
        attributes={
            _GEN_AI_OPERATION_NAME: "create_agent",
            _GEN_AI_AGENT_NAME: "worker",
        },
    ):
        pass

    assert adapter.events == []
    assert adapter.evaluation_count == 0


def test_enforce_true_emits_warning() -> None:
    """OTel spans observe post-hoc; enforce=True can't actually block.
    The adapter must warn so users don't assume otherwise."""
    with pytest.warns(UserWarning, match="enforce=True"):
        AutoGenAdapter(profile=_profile(), enforce=True)


def test_instrument_set_global_false_does_not_touch_global_tracer_provider() -> None:
    """Power-user path: caller manages the global TracerProvider themselves."""
    before = trace.get_tracer_provider()
    adapter = AutoGenAdapter(profile=_profile())
    tp = adapter.instrument(set_global=False)
    after = trace.get_tracer_provider()
    assert before is after
    # Returned provider still has our processor wired up.
    tracer = tp.get_tracer("autogen-core")
    with tracer.start_as_current_span(
        "execute_tool s",
        attributes={
            _GEN_AI_OPERATION_NAME: "execute_tool",
            _GEN_AI_TOOL_NAME: "s",
        },
    ):
        pass
    assert [e.event_type for e in adapter.events] == ["pre_tool_use", "post_tool_use"]
