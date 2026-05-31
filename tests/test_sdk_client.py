"""Tests for the registry-based adapter factory on AgentegrityClient.

The `_BaseAdapter` subclass / wiring tests live in
``test_adapter_conformance.py``. This module focuses on the
``create_adapter(name, ...)`` entry point itself: name lookup,
unknown-adapter error path, and forwarding of constructor kwargs.
"""

from __future__ import annotations

import pytest

from agentegrity import AgentegrityClient
from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.profile import AgentProfile


def test_create_adapter_returns_base_adapter_subclass() -> None:
    client = AgentegrityClient()
    adapter = client.create_adapter("claude", profile=AgentProfile.default())
    assert isinstance(adapter, _BaseAdapter)
    assert adapter.name == "claude"


def test_create_adapter_wires_clients_evaluator() -> None:
    """The factory must reuse the client's evaluator, not construct a fresh one."""
    client = AgentegrityClient()
    adapter = client.create_adapter("claude", profile=AgentProfile.default())
    assert adapter._evaluator is client.evaluator


def test_create_adapter_forwards_enforce_and_api_key() -> None:
    client = AgentegrityClient()
    adapter = client.create_adapter(
        "claude",
        profile=AgentProfile.default(),
        enforce=True,
        api_key="sk-test",
    )
    assert adapter._enforce is True
    assert adapter._api_key == "sk-test"


def test_create_adapter_unknown_name_raises_with_valid_options() -> None:
    client = AgentegrityClient()
    with pytest.raises(ValueError) as excinfo:
        client.create_adapter("nonexistent_framework", profile=AgentProfile.default())
    msg = str(excinfo.value)
    assert "nonexistent_framework" in msg
    # The error message must list valid names so the caller can fix the typo.
    assert "claude" in msg
    assert "langchain" in msg


@pytest.mark.parametrize(
    "name,expected_class_name",
    [
        ("claude", "ClaudeAdapter"),
        ("langchain", "LangChainAdapter"),
        ("openai_agents", "OpenAIAgentsAdapter"),
        ("crewai", "CrewAIAdapter"),
        ("google_adk", "GoogleADKAdapter"),
    ],
)
def test_create_adapter_resolves_every_shipped_name(
    name: str, expected_class_name: str
) -> None:
    """Every name in the registry must resolve to the documented class."""
    client = AgentegrityClient()
    adapter = client.create_adapter(name, profile=AgentProfile.default())
    assert type(adapter).__name__ == expected_class_name
