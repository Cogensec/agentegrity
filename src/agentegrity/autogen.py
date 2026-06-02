"""Zero-config AutoGen instrumentation.

The fastest way to add agentegrity to an AutoGen team::

    from autogen_agentchat.teams import RoundRobinGroupChat
    from agentegrity.autogen import instrument, report

    instrument()  # installs our SpanProcessor on the global OTel provider
    team = RoundRobinGroupChat([agent1, agent2])
    await team.run(task="...")
    print(report())

``instrument()`` lazily constructs a process-global
:class:`~agentegrity.adapters.autogen.AutoGenAdapter`, builds an OTel
:class:`TracerProvider` wired to the adapter's
:class:`SpanProcessor`, and installs that provider via
``opentelemetry.trace.set_tracer_provider``. AutoGen's
``invoke_agent``/``execute_tool`` spans use the global tracer, so
this is the wiring step that makes them visible to agentegrity.

Power users who already manage their own ``TracerProvider`` should
call ``adapter().span_processor()`` and attach it to their own
provider; do not call ``instrument()`` in that case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.autogen import AutoGenAdapter

__all__ = ["adapter", "instrument", "register_exporter", "report", "reset"]


_default: AutoGenAdapter | None = None


def _default_adapter() -> AutoGenAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter("autogen", profile=AgentProfile.default())
    return _default


def adapter() -> AutoGenAdapter:
    return _default_adapter()


def instrument(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
    set_global: bool = True,
) -> AutoGenAdapter:
    """Install agentegrity's SpanProcessor on the global OTel TracerProvider.

    Returns the wired adapter for the caller to keep references to (for
    ``register_exporter``, custom reads of ``adapter.events``, etc.).
    """
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: AutoGenAdapter = effective_client.create_adapter(
            "autogen",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
    else:
        ad = _default_adapter()
    ad.instrument(set_global=set_global)
    return ad


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "autogen",
            "agent_id": None,
            "evaluations": 0,
            "events": 0,
            "attestation_records": 0,
            "chain_valid": True,
            "enforce_mode": False,
        }
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    global _default
    _default = None
