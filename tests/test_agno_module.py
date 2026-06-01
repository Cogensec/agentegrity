"""Tests for the agentegrity.agno zero-config surface."""

from __future__ import annotations

from typing import Any, Generator

import pytest

pytest.importorskip("agno")

import agentegrity.agno as ag
from agentegrity.core.profile import AgentProfile


class _HookTarget:
    def __init__(self, name: str = "agent") -> None:
        self.name = name
        self.pre_hooks: list[Any] | None = None
        self.post_hooks: list[Any] | None = None
        self.tool_hooks: list[Any] | None = None


@pytest.fixture(autouse=True)
def _clean() -> Generator[None, None, None]:
    ag.reset()
    yield
    ag.reset()


def test_report_before_instrument_returns_empty() -> None:
    summary = ag.report()
    assert summary["adapter"] == "agno"
    assert summary["evaluations"] == 0
    assert summary["chain_valid"] is True


def test_adapter_lazy_construction() -> None:
    first = ag.adapter()
    second = ag.adapter()
    assert first is second
    assert first.name == "agno"


def test_instrument_returns_agent_and_attaches_hooks() -> None:
    target = _HookTarget()
    returned = ag.instrument(target)  # type: ignore[arg-type]
    assert returned is target
    assert target.pre_hooks is not None
    assert target.tool_hooks is not None


def test_instrument_with_explicit_profile_isolates_global() -> None:
    target = _HookTarget()
    ag.instrument(target, profile=AgentProfile.default(name="explicit"))  # type: ignore[arg-type]
    assert ag._default is None


def test_reset_discards_module_global() -> None:
    first = ag.adapter()
    ag.reset()
    second = ag.adapter()
    assert first is not second
