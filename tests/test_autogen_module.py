"""Tests for the agentegrity.autogen zero-config surface."""

from __future__ import annotations

from typing import Generator

import pytest

pytest.importorskip("opentelemetry.sdk.trace")

import agentegrity.autogen as ag
from agentegrity.core.profile import AgentProfile


@pytest.fixture(autouse=True)
def _clean() -> Generator[None, None, None]:
    ag.reset()
    yield
    ag.reset()


def test_report_before_instrument_returns_empty() -> None:
    summary = ag.report()
    assert summary["adapter"] == "autogen"
    assert summary["evaluations"] == 0
    assert summary["chain_hash_linked"] is True


def test_adapter_lazy_construction() -> None:
    first = ag.adapter()
    second = ag.adapter()
    assert first is second
    assert first.name == "autogen"


def test_instrument_default_path_returns_adapter() -> None:
    # set_global=False so the test doesn't clobber the process-global
    # OTel TracerProvider for any later test in the same run.
    adapter = ag.instrument(set_global=False)
    assert adapter is ag.adapter()
    assert adapter.name == "autogen"


def test_instrument_with_explicit_profile_isolates_global() -> None:
    """Explicit profile must not touch the module-global adapter."""
    adapter = ag.instrument(
        profile=AgentProfile.default(name="explicit"),
        set_global=False,
    )
    assert ag._default is None
    assert adapter.profile.name == "explicit"


def test_reset_discards_module_global() -> None:
    first = ag.adapter()
    ag.reset()
    second = ag.adapter()
    assert first is not second
