"""
Zero-config OpenAI Agents SDK instrumentation.

The fastest way to add agentegrity to an agent built on the OpenAI
Agents SDK:

    from agents import Agent, Runner
    from agentegrity.openai_agents import instrument, run_hooks, report

    agent = instrument(Agent(...))
    await Runner.run(agent, input="...", hooks=run_hooks())
    print(report())

``instrument()`` gates each function tool so a denied call reaches the model
as a denial and the run continues; ``run_hooks()`` records the run and gates
the tools that carry no guardrail. Use both from the same adapter: the
module default, or ``adapter.instrument()`` and ``adapter.create_run_hooks()``
on one adapter you built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.adapters.base import empty_summary
from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

__all__ = ["adapter", "instrument", "register_exporter", "report", "reset", "run_hooks"]


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


def _resolve(
    profile: AgentProfile | None,
    client: AgentegrityClient | None,
    enforce: bool,
    on_deny: str,
) -> OpenAIAgentsAdapter:
    if profile is not None or client is not None or enforce or on_deny != "continue":
        effective_client = client or AgentegrityClient()
        ad: OpenAIAgentsAdapter = effective_client.create_adapter(
            "openai_agents",
            profile=profile or AgentProfile.default(),
            enforce=enforce,
            on_deny=on_deny,
        )
        return ad
    return _default_adapter()


def instrument(
    agent: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    on_deny: str = "continue",
) -> Any:
    """Gate every function tool ``agent`` can reach; returns the agent."""
    return _resolve(profile, client, enforce, on_deny).instrument(agent)


def run_hooks(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    on_deny: str = "continue",
) -> Any:
    """Return a ``RunHooks`` instance ready for ``Runner.run(..., hooks=...)``."""
    return _resolve(profile, client, enforce, on_deny).create_run_hooks()


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return empty_summary("openai_agents")
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    """Register a :class:`SessionExporter` on the module-global adapter."""
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    global _default
    _default = None
