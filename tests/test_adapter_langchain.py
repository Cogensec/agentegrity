"""Tests for the LangChain / LangGraph adapter."""

from __future__ import annotations

import sys
import types
from typing import Any
from uuid import uuid4

import pytest

from agentegrity.adapters.langchain import LangChainAdapter
from agentegrity.core.profile import AgentProfile


@pytest.fixture
def stub_langchain(monkeypatch: pytest.MonkeyPatch) -> None:
    callbacks_mod = types.ModuleType("langchain_core.callbacks")

    class BaseCallbackHandler:  # minimal stub
        pass

    callbacks_mod.BaseCallbackHandler = BaseCallbackHandler  # type: ignore[attr-defined]
    root = types.ModuleType("langchain_core")
    root.callbacks = callbacks_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_core", root)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", callbacks_mod)


def test_adapter_name() -> None:
    ad = LangChainAdapter(profile=AgentProfile.default())
    assert ad.name == "langchain"
    assert ad.evaluation_count == 0


@pytest.mark.asyncio
async def test_on_event_pre_tool_use() -> None:
    ad = LangChainAdapter(profile=AgentProfile.default())
    await ad.on_event(
        "pre_tool_use", {"tool_name": "search", "tool_input": {"q": "llm"}}
    )
    assert ad.evaluation_count == 1
    assert ad.get_collected_context()["tool_usage"]["search"] == 1


def test_callback_handler_fires_events(stub_langchain: None) -> None:
    ad = LangChainAdapter(profile=AgentProfile.default())
    handler = ad.create_callback_handler()

    run_id = uuid4()
    handler.on_chain_start(
        {"name": "root"}, {"input": "hello"}, run_id=run_id, parent_run_id=None
    )
    handler.on_tool_start(
        {"name": "search"}, "q=llm", run_id=uuid4(), parent_run_id=run_id
    )
    handler.on_tool_end("result", run_id=run_id, parent_run_id=run_id, name="search")
    handler.on_chain_end({"out": "done"}, run_id=run_id, parent_run_id=None)

    # Events flushed asynchronously via asyncio.run in _dispatch
    assert ad.evaluation_count >= 1


def test_instrument_chain_with_config(stub_langchain: None) -> None:
    ad = LangChainAdapter(profile=AgentProfile.default())

    captured: dict[str, Any] = {}

    class FakeRunnable:
        def with_config(self, cfg: dict[str, Any]) -> "FakeRunnable":
            captured["cfg"] = cfg
            return self

    wrapped = ad.instrument_chain(FakeRunnable())
    assert wrapped is not None
    assert "callbacks" in captured["cfg"]
    assert len(captured["cfg"]["callbacks"]) == 1


def test_instrument_graph_uses_with_config(stub_langchain: None) -> None:
    ad = LangChainAdapter(profile=AgentProfile.default())

    calls: list[dict[str, Any]] = []

    class FakeGraph:
        def with_config(self, cfg: dict[str, Any]) -> "FakeGraph":
            calls.append(cfg)
            return self

    ad.instrument_graph(FakeGraph())
    assert len(calls) == 1 and "callbacks" in calls[0]


def test_create_callback_handler_requires_langchain_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force the import to fail even when langchain-core is installed
    # (the coverage CI job installs [all,dev]). Setting the module
    # entry to None makes Python raise ModuleNotFoundError on import.
    monkeypatch.setitem(sys.modules, "langchain_core", None)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", None)
    ad = LangChainAdapter(profile=AgentProfile.default())
    with pytest.raises(ImportError, match="langchain-core"):
        ad.create_callback_handler()
