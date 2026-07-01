"""Tests for the agentegrity.langchain zero-config surface."""

from __future__ import annotations

import sys
import types

import pytest

import agentegrity.langchain as al
from agentegrity.core.profile import AgentProfile


@pytest.fixture
def stub_langchain(monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks_mod = types.ModuleType("langchain_core.callbacks")

    class BaseCallbackHandler:
        pass

    callbacks_mod.BaseCallbackHandler = BaseCallbackHandler  # type: ignore[attr-defined]
    root = types.ModuleType("langchain_core")
    root.callbacks = callbacks_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_core", root)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", callbacks_mod)


@pytest.fixture(autouse=True)
def _clean_default() -> None:
    al.reset()
    yield
    al.reset()


def test_report_before_instrument_returns_empty() -> None:
    summary = al.report()
    assert summary["adapter"] == "langchain"
    assert summary["evaluations"] == 0
    assert summary["chain_hash_linked"] is True


def test_callback_handler_lazy_default(stub_langchain: None) -> None:
    h = al.callback_handler()
    assert h is not None
    first = al.adapter()
    second = al.adapter()
    assert first is second


def test_callback_handler_explicit_profile_isolates_global(stub_langchain: None) -> None:
    custom = AgentProfile.default(name="custom-lc")
    al.callback_handler(profile=custom)
    assert al._default is None


def test_instrument_chain_wraps(stub_langchain: None) -> None:
    class FakeRunnable:
        def with_config(self, cfg: dict) -> "FakeRunnable":
            self.cfg = cfg
            return self

    r = al.instrument_chain(FakeRunnable())
    assert r is not None
    assert "callbacks" in r.cfg  # type: ignore[attr-defined]
