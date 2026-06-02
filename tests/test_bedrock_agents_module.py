"""Tests for the agentegrity.bedrock_agents zero-config surface."""

from __future__ import annotations

from typing import Any, Generator

import pytest

pytest.importorskip("strands")

import agentegrity.bedrock_agents as ba


@pytest.fixture(autouse=True)
def _clean() -> Generator[None, None, None]:
    ba.reset()
    yield
    ba.reset()


def test_report_before_instrument_returns_empty() -> None:
    summary = ba.report()
    assert summary["adapter"] == "bedrock_agents"
    assert summary["evaluations"] == 0
    assert summary["chain_valid"] is True


def test_adapter_lazy_construction() -> None:
    first = ba.adapter()
    second = ba.adapter()
    assert first is second
    assert first.name == "bedrock_agents"


def test_instrument_strands_returns_agent() -> None:
    from strands.hooks import HookRegistry

    class _FakeAgent:
        def __init__(self) -> None:
            self.name = "agent"
            self.hooks = HookRegistry()

    agent = _FakeAgent()
    returned = ba.instrument_strands(agent)  # type: ignore[arg-type]
    assert returned is agent
    assert agent.hooks.has_callbacks()


def test_wrap_client_returns_same_client_with_patched_invoke() -> None:
    class _Client:
        def invoke_agent(self, **kwargs: Any) -> dict[str, Any]:
            return {"completion": iter([])}

    c = _Client()
    original = c.invoke_agent
    wrapped = ba.wrap_client(c)
    assert wrapped is c
    assert wrapped.invoke_agent is not original


def test_reset_discards_module_global() -> None:
    first = ba.adapter()
    ba.reset()
    second = ba.adapter()
    assert first is not second
