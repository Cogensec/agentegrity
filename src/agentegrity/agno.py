"""Zero-config Agno instrumentation.

The fastest way to add agentegrity to an Agno agent::

    from agno.agent import Agent
    from agentegrity.agno import instrument, report

    agent = instrument(Agent(...))
    agent.run("...")
    print(report())

For multi-agent teams::

    from agentegrity.agno import instrument_team, report
    team = instrument_team(team)
    team.run("...")

``instrument`` / ``instrument_team`` lazily construct a process-global
:class:`~agentegrity.adapters.agno.AgnoAdapter` and attach hooks to the
passed agent/team. ``report()`` returns the current session summary.
Call ``reset()`` between sessions or in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.team import Team

    from agentegrity.adapters.agno import AgnoAdapter

__all__ = [
    "adapter",
    "instrument",
    "instrument_team",
    "register_exporter",
    "report",
    "reset",
]


_default: AgnoAdapter | None = None


def _default_adapter() -> AgnoAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter("agno", profile=AgentProfile.default())
    return _default


def adapter() -> AgnoAdapter:
    return _default_adapter()


def _resolve(
    profile: AgentProfile | None,
    client: AgentegrityClient | None,
    enforce: bool,
    api_key: str | None,
) -> AgnoAdapter:
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: AgnoAdapter = effective_client.create_adapter(
            "agno",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
        return ad
    return _default_adapter()


def instrument(
    agent: Agent,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Agent:
    """Attach agentegrity hooks to a single Agno agent. Returns the agent."""
    ad = _resolve(profile, client, enforce, api_key)
    return ad.instrument(agent)


def instrument_team(
    team: Team,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Team:
    """Attach agentegrity hooks across an Agno team and its members. Returns the team."""
    ad = _resolve(profile, client, enforce, api_key)
    return ad.instrument_team(team)


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "agno",
            "agent_id": None,
            "evaluations": 0,
            "events": 0,
            "attestation_records": 0,
            "chain_hash_linked": True,
            "enforce_mode": False,
        }
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    global _default
    _default = None
