"""Tests for the agentegrity.crewai zero-config surface."""

from __future__ import annotations

import sys

import pytest

import agentegrity.crewai as ac
from agentegrity.core.profile import AgentProfile


@pytest.fixture(autouse=True)
def _clean() -> None:
    ac.reset()
    yield
    ac.reset()


def test_report_before_instrument_returns_empty() -> None:
    summary = ac.report()
    assert summary["adapter"] == "crewai"
    assert summary["evaluations"] == 0


def test_adapter_lazy_construction() -> None:
    first = ac.adapter()
    second = ac.adapter()
    assert first is second


def test_instrument_requires_crewai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "crewai.events", None)
    with pytest.raises(ImportError, match="crewai"):
        ac.instrument()


def test_instrument_with_explicit_profile_isolates_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "crewai.events", None)
    with pytest.raises(ImportError):
        ac.instrument(profile=AgentProfile.default(name="explicit"))
    assert ac._default is None
