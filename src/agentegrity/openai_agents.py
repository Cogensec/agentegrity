"""
Zero-config OpenAI Agents SDK instrumentation.

The fastest way to add agentegrity to an agent built on the OpenAI
Agents SDK:

    from agents import Agent, Runner
    from agentegrity.openai_agents import run_hooks, report

    await Runner.run(Agent(...), input="...", hooks=run_hooks())
    print(report())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

__all__ = ["adapter", "register_exporter", "report", "reset", "run_hooks"]


_default: OpenAIAgentsAdapter | None = None


def _default_adapter() -> OpenAIAgentsAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter(
            "openai_agents", profile=AgentProfile.default()
        )
    return _default


def adapter() -> OpenAIAgentsAdapter:
    return _default_adapter()


def run_hooks(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Any:
    """Return a ``RunHooks`` instance ready for ``Runner.run(..., hooks=...)``."""
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: OpenAIAgentsAdapter = effective_client.create_adapter(
            "openai_agents",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
    else:
        ad = _default_adapter()
    return ad.create_run_hooks()


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "openai_agents",
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
