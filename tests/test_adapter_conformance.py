"""Cross-adapter conformance suite.

Every Python framework adapter inherits from
:class:`agentegrity.adapters.base._BaseAdapter` and exposes the same
framework-agnostic ``on_event`` lifecycle. Framework-specific glue
(`subscribe`, `hooks`, `instrument`, etc.) wraps this same shared core,
so a regression in any one adapter usually means the base contract has
drifted.

This suite parametrises the same canonical event stream across all five
shipped adapters and pins the invariants every conforming adapter must
honour. A new adapter (Semantic Kernel, AutoGen, AWS Bedrock Agents) is
expected to add a fixture entry below and the rest of the matrix
applies automatically.

Invariants pinned here:

  * ``name`` matches the documented ``_name`` for each adapter.
  * ``evaluation_count`` equals the number of evaluation-producing
    events processed.
  * ``attestation_chain`` length equals ``evaluation_count`` and
    ``verify_chain()`` returns True.
  * ``session_id`` is stable across events.
  * Registered exporters receive ``on_session_start`` exactly once,
    ``on_event`` per emitted event, and ``on_session_end`` on
    ``close()``.
  * ``register_exporter`` is idempotent (re-registering the same
    instance does not cause double fan-out).
  * A broken exporter (raises in any callback) does not crash the
    adapter.
  * ``get_summary()`` reports a consistent shape: keys present, types
    correct, ``adapter`` field matches the adapter name.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.adapters.claude import ClaudeAdapter
from agentegrity.adapters.crewai import CrewAIAdapter
from agentegrity.adapters.google_adk import GoogleADKAdapter
from agentegrity.adapters.langchain import LangChainAdapter
from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter
from agentegrity.core.profile import (
    AgentProfile,
    AgentType,
    DeploymentContext,
    RiskTier,
)

# Every shipped adapter is registered here. New adapters add one line
# and inherit the entire matrix below.
ADAPTER_CLASSES: list[tuple[str, type[_BaseAdapter]]] = [
    ("claude", ClaudeAdapter),
    ("langchain", LangChainAdapter),
    ("openai_agents", OpenAIAgentsAdapter),
    ("crewai", CrewAIAdapter),
    ("google_adk", GoogleADKAdapter),
]


def _profile() -> AgentProfile:
    return AgentProfile(
        name="conformance",
        agent_type=AgentType.TOOL_USING,
        capabilities=["tool_use", "memory_access"],
        deployment_context=DeploymentContext.CLOUD,
        risk_tier=RiskTier.MEDIUM,
    )


# Canonical event stream. The base adapter recognises eight event
# types; we cover the four that produce evaluations + state changes.
# Adapter-private events are intentionally not exercised here — that's
# what each framework's adapter-specific test module is for.
def _canonical_event_stream() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "user_prompt_submit",
            {"prompt": "What's the capital of France?"},
        ),
        (
            "pre_tool_use",
            {"tool_name": "search", "tool_input": {"query": "Paris weather"}},
        ),
        (
            "post_tool_use",
            {
                "tool_name": "search",
                "tool_response": "Paris is sunny, 22C.",
            },
        ),
        ("stop", {"reason": "completed"}),
    ]


class _CapturingExporter:
    """Records every callback the adapter fires at it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def on_session_start(
        self, session_id: str, adapter_name: str, profile: dict[str, Any]
    ) -> None:
        self.calls.append(("on_session_start", (session_id, adapter_name)))

    async def on_event(self, session_id: str, event: dict[str, Any]) -> None:
        self.calls.append(("on_event", (session_id, event["event_type"])))

    async def on_session_end(
        self, session_id: str, summary: dict[str, Any]
    ) -> None:
        self.calls.append(("on_session_end", (session_id,)))


class _BrokenExporter:
    """Raises in every callback. The adapter must fail open."""

    async def on_session_start(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("exporter intentionally broken")

    async def on_event(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("exporter intentionally broken")

    async def on_session_end(self, *_: Any, **__: Any) -> None:
        raise RuntimeError("exporter intentionally broken")


def _build(cls: type[_BaseAdapter]) -> _BaseAdapter:
    return cls(profile=_profile())


async def _drive(adapter: _BaseAdapter) -> int:
    """Run the canonical event stream through the adapter.

    Returns the count of events that actually triggered an evaluation
    (any handler that calls ``_run_evaluation`` increments
    ``evaluation_count``).
    """
    stream = _canonical_event_stream()
    pre_count = adapter.evaluation_count
    for event_type, data in stream:
        await adapter.on_event(event_type, data)
    return adapter.evaluation_count - pre_count


@pytest.mark.parametrize(
    "expected_name,adapter_cls",
    ADAPTER_CLASSES,
    ids=[name for name, _ in ADAPTER_CLASSES],
)
class TestAdapterConformance:
    """Every test method runs against every shipped adapter."""

    def test_inherits_base_adapter(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        assert isinstance(adapter, _BaseAdapter)
        assert adapter.name == expected_name

    def test_event_stream_produces_attestation_chain(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        n_evals = asyncio.run(_drive(adapter))
        assert n_evals > 0, f"{expected_name} produced no evaluations"
        assert adapter.evaluation_count == n_evals
        assert len(adapter.attestation_chain.records) == n_evals
        assert adapter.attestation_chain.verify_chain()

    def test_session_id_stable_across_events(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        first = adapter.session_id
        asyncio.run(_drive(adapter))
        assert adapter.session_id == first
        # Session ids look like uuid4 hex.
        assert len(first) == 32
        assert all(c in "0123456789abcdef" for c in first)

    def test_exporter_receives_lifecycle(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        exporter = _CapturingExporter()
        adapter.register_exporter(exporter)
        asyncio.run(_drive(adapter))
        adapter.close()

        # Drain any background coroutines the exporter fan-out
        # scheduled. Exporter callbacks are fire-and-forget when there
        # is no event loop running, but tests run under pytest with no
        # ambient loop, so notifications complete synchronously via the
        # asyncio.run path inside _notify_exporters.
        names = [c[0] for c in exporter.calls]
        assert names.count("on_session_start") == 1, (
            f"{expected_name}: expected 1 session_start, got {names}"
        )
        assert "on_event" in names, f"{expected_name}: no events fanned out"
        assert names.count("on_session_end") == 1, (
            f"{expected_name}: expected 1 session_end, got {names}"
        )
        # session_id should be the same across every callback.
        session_ids = {c[1][0] for c in exporter.calls if c[1]}
        assert len(session_ids) == 1, (
            f"{expected_name}: callbacks span multiple session ids: {session_ids}"
        )
        assert next(iter(session_ids)) == adapter.session_id

    def test_register_exporter_idempotent(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        exporter = _CapturingExporter()
        adapter.register_exporter(exporter)
        adapter.register_exporter(exporter)  # should be a no-op
        adapter.register_exporter(exporter)  # also a no-op
        asyncio.run(_drive(adapter))
        adapter.close()
        # session_start fires exactly once even though the exporter was
        # registered three times.
        names = [c[0] for c in exporter.calls]
        assert names.count("on_session_start") == 1

    def test_broken_exporter_does_not_crash_adapter(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        adapter.register_exporter(_BrokenExporter())
        # Driving the stream must not raise. Adapter state must be
        # populated as if no exporter were registered.
        n_evals = asyncio.run(_drive(adapter))
        adapter.close()
        assert n_evals > 0
        assert adapter.attestation_chain.verify_chain()

    def test_multiple_exporters_all_receive_events(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        e1 = _CapturingExporter()
        e2 = _CapturingExporter()
        adapter.register_exporter(e1)
        adapter.register_exporter(e2)
        asyncio.run(_drive(adapter))
        adapter.close()
        # Both exporters see the same lifecycle.
        for exp in (e1, e2):
            names = [c[0] for c in exp.calls]
            assert names.count("on_session_start") == 1, expected_name
            assert names.count("on_session_end") == 1, expected_name
            assert "on_event" in names, expected_name

    def test_get_summary_shape(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        asyncio.run(_drive(adapter))
        summary = adapter.get_summary()
        assert summary["adapter"] == expected_name
        assert isinstance(summary["agent_id"], str)
        assert isinstance(summary["evaluations"], int)
        assert summary["evaluations"] == adapter.evaluation_count
        assert isinstance(summary["events"], int)
        assert summary["events"] == len(adapter.events)
        assert isinstance(summary["attestation_records"], int)
        assert summary["attestation_records"] == len(
            adapter.attestation_chain.records
        )
        assert summary["chain_valid"] is True
        assert summary["enforce_mode"] is False

    def test_close_is_idempotent(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        adapter.register_exporter(_CapturingExporter())
        asyncio.run(_drive(adapter))
        adapter.close()
        adapter.close()
        # Capture across every exporter to confirm session_end fired
        # exactly once even with two close() calls.
        for exporter in adapter._exporters:
            names = [c[0] for c in getattr(exporter, "calls", [])]
            assert names.count("on_session_end") == 1, expected_name

    def test_unknown_event_is_ignored(
        self, expected_name: str, adapter_cls: type[_BaseAdapter]
    ) -> None:
        adapter = _build(adapter_cls)
        # Unknown event types must be tolerated quietly so a future
        # framework version can add events without breaking older
        # adapter installations.
        asyncio.run(adapter.on_event("totally_made_up_event", {"x": 1}))
        # No evaluation happened.
        assert adapter.evaluation_count == 0
        assert len(adapter.attestation_chain.records) == 0


class TestAdapterRegistryStable:
    """Sanity: a maintainer adding/removing an adapter must update this
    list deliberately."""

    def test_five_default_adapters_shipped(self) -> None:
        # If this fails because you added a new adapter, also add a
        # row to ADAPTER_CLASSES at the top of this module so the
        # conformance matrix runs against your new adapter too.
        assert len(ADAPTER_CLASSES) == 5
        names = {name for name, _ in ADAPTER_CLASSES}
        assert names == {
            "claude",
            "langchain",
            "openai_agents",
            "crewai",
            "google_adk",
        }
