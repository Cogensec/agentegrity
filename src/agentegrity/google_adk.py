"""
Zero-config Google ADK instrumentation.

The fastest way to add agentegrity to a Google ADK agent:

    from google.adk.agents import LlmAgent
    from agentegrity.google_adk import instrument, report

    agent = LlmAgent(name="my-agent", ...)
    instrument(agent)
    # run via google.adk.runners.Runner
    print(report())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.google_adk import GoogleADKAdapter

__all__ = ["adapter", "instrument", "register_exporter", "report", "reset"]


_default: GoogleADKAdapter | None = None


def _default_adapter() -> GoogleADKAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter(
            "google_adk", profile=AgentProfile.default()
        )
    return _default


def adapter() -> GoogleADKAdapter:
    return _default_adapter()


def instrument(
    agent: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Any:
    """Attach agentegrity callbacks to a Google ADK agent."""
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: GoogleADKAdapter = effective_client.create_adapter(
            "google_adk",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
    else:
        ad = _default_adapter()
    return ad.instrument(agent)


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "google_adk",
            "agent_id": None,
            "evaluations": 0,
            "events": 0,
            "attestation_records": 0,
            "chain_hash_linked": True,
            "enforce_mode": False,
        }
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    """Register a :class:`SessionExporter` on the module-global adapter."""
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    global _default
    _default = None
