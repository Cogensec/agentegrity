"""
Zero-config CrewAI instrumentation.

The fastest way to add agentegrity to an existing CrewAI crew:

    from agentegrity.crewai import instrument, report
    instrument()             # subscribe to the global event bus
    crew.kickoff()
    print(report())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.crewai import CrewAIAdapter

__all__ = ["adapter", "instrument", "register_exporter", "report", "reset"]


_default: CrewAIAdapter | None = None


def _default_adapter() -> CrewAIAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter("crewai", profile=AgentProfile.default())
    return _default


def adapter() -> CrewAIAdapter:
    return _default_adapter()


def instrument(
    crew: Any | None = None,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> CrewAIAdapter:
    """Subscribe the agentegrity adapter to the CrewAI event bus."""
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: CrewAIAdapter = effective_client.create_adapter(
            "crewai",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
    else:
        ad = _default_adapter()
    ad.subscribe(crew)
    return ad


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "crewai",
            "agent_id": None,
            "evaluations": 0,
            "events": 0,
            "attestation_records": 0,
            "chain_valid": True,
            "enforce_mode": False,
        }
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    """Register a :class:`SessionExporter` on the module-global adapter."""
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    global _default
    _default = None
