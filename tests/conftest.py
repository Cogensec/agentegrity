"""Shared pytest fixtures for the agentegrity test suite."""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture
def stub_langchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a minimal fake ``langchain_core`` into ``sys.modules``.

    The LangChain adapter subclasses ``BaseCallbackHandler`` at
    instrument time, so ``create_callback_handler`` imports
    ``langchain_core.callbacks``. Tests that exercise the adapter with a
    mock graph (rather than a real compiled LangGraph) need that import
    to succeed without the heavy ``langchain-core`` dependency installed.
    """
    callbacks_mod = types.ModuleType("langchain_core.callbacks")

    class BaseCallbackHandler:  # minimal stub
        pass

    callbacks_mod.BaseCallbackHandler = BaseCallbackHandler  # type: ignore[attr-defined]
    root = types.ModuleType("langchain_core")
    root.callbacks = callbacks_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_core", root)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", callbacks_mod)
