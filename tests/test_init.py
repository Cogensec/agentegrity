"""Tests for the two-line entry point: agentegrity.init() / shutdown().

Framework detection is probed against the real environment (CI's
coverage job installs [all], so every framework resolves there);
dispatch and lifecycle semantics use stub objects so no framework
actually has to run.
"""

from __future__ import annotations

import warnings

import pytest

import agentegrity
from agentegrity.sdk.runtime import (
    AgentegrityRuntime,
    detect_frameworks,
)


@pytest.fixture(autouse=True)
def clean_runtime():
    """Every test starts and ends without a global runtime."""
    agentegrity.shutdown()
    yield
    agentegrity.shutdown()


class _StubExporter:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.ended: list[str] = []

    def on_session_start(self, session_id, adapter_name, profile) -> None:
        self.started.append(session_id)

    def on_event(self, session_id, event) -> None:
        pass

    def on_session_end(self, session_id, summary) -> None:
        self.ended.append(session_id)


class _FakeRunnable:
    """Quacks like a LangChain runnable for dispatch tests."""

    __module__ = "langchain_core.runnables.base"

    def __init__(self) -> None:
        self.config_calls: list[dict] = []

    def with_config(self, config):
        self.config_calls.append(config)
        return self


class TestDetectFrameworks:
    def test_detects_installed_frameworks(self):
        detected = detect_frameworks()
        # The dev environment installs [all]; at minimum langchain and
        # crewai must resolve there. Guard so the test degrades to a
        # skip instead of a lie in a minimal environment.
        if not detected:
            pytest.skip("no frameworks installed in this environment")
        assert set(detected) <= set(agentegrity.sdk.client._ADAPTER_REGISTRY)

    def test_unknown_framework_rejected(self):
        with pytest.raises(ValueError, match="Unknown framework"):
            agentegrity.init(frameworks=["not_a_framework"])


class TestInitLifecycle:
    def test_init_returns_runtime_with_requested_adapters(self):
        runtime = agentegrity.init(frameworks=["langchain"])
        assert isinstance(runtime, AgentegrityRuntime)
        assert set(runtime.adapters) == {"langchain"}

    def test_init_is_idempotent(self):
        first = agentegrity.init(frameworks=["langchain"])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            second = agentegrity.init(frameworks=["langchain"])
        assert second is first
        assert any("already initialised" in str(w.message) for w in caught)

    def test_shutdown_ends_sessions_and_resets(self):
        runtime = agentegrity.init(frameworks=["langchain"])
        exporter = _StubExporter()
        runtime.adapters["langchain"].register_exporter(exporter)
        agentegrity.shutdown()
        assert len(exporter.ended) == 1
        # After shutdown a new init produces a fresh runtime.
        assert agentegrity.init(frameworks=["langchain"]) is not runtime

    def test_shutdown_without_init_is_noop(self):
        agentegrity.shutdown()
        agentegrity.shutdown()

    def test_crewai_auto_subscribes(self, monkeypatch):
        pytest.importorskip("crewai")
        from agentegrity.adapters.crewai import CrewAIAdapter

        calls: list[object] = []

        def fake_subscribe(self, crew=None, **kwargs):
            calls.append(crew)

        monkeypatch.setattr(CrewAIAdapter, "subscribe", fake_subscribe)
        agentegrity.init(frameworks=["crewai"])
        assert calls == [None]

    def test_enforce_forwarded_to_adapters(self):
        runtime = agentegrity.init(frameworks=["langchain"], enforce=True)
        assert runtime.adapters["langchain"].get_summary()["enforce_mode"] is True


class TestInstrumentDispatch:
    def test_langchain_runnable_dispatches_to_instrument_chain(self):
        # Adapter construction is lazy, but instrument_chain builds a
        # real callback handler, which needs langchain-core installed.
        pytest.importorskip("langchain_core")
        runtime = agentegrity.init(frameworks=["langchain"])
        chain = _FakeRunnable()
        instrumented = runtime.instrument(chain)
        assert instrumented is chain
        assert len(chain.config_calls) == 1
        assert "callbacks" in chain.config_calls[0]

    def test_unrecognised_object_raises(self):
        runtime = agentegrity.init(frameworks=["langchain"])
        with pytest.raises(TypeError, match="Cannot infer a framework"):
            runtime.instrument(object())

    def test_framework_not_initialised_raises(self):
        runtime = agentegrity.init(frameworks=["langchain"])

        class _FakeAgnoAgent:
            __module__ = "agno.agent"

        with pytest.raises(RuntimeError, match="agno"):
            runtime.instrument(_FakeAgnoAgent())


class TestReport:
    def test_report_keyed_by_adapter(self):
        runtime = agentegrity.init(frameworks=["langchain"])
        report = runtime.report()
        assert set(report) == {"langchain"}
        assert report["langchain"]["adapter"] == "langchain"
