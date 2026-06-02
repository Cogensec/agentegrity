"""
Zero-config LangChain / LangGraph instrumentation.

Covers plain LangChain chains/agents and LangGraph compiled graphs with
a single module. The fastest way to add agentegrity to an existing
LangChain or LangGraph project:

    from agentegrity.langchain import instrument_chain, report
    chain = instrument_chain(my_chain)
    chain.invoke({"input": "..."})
    print(report())

    # LangGraph:
    from agentegrity.langchain import instrument_graph, report
    graph = instrument_graph(my_compiled_graph)
    graph.invoke(state)
    print(report())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.langchain import LangChainAdapter

__all__ = [
    "adapter",
    "callback_handler",
    "instrument_chain",
    "instrument_graph",
    "register_exporter",
    "report",
    "reset",
]


_default: LangChainAdapter | None = None


def _default_adapter() -> LangChainAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter("langchain", profile=AgentProfile.default())
    return _default


def adapter() -> LangChainAdapter:
    return _default_adapter()


def _resolve(
    profile: AgentProfile | None,
    client: AgentegrityClient | None,
    enforce: bool,
    api_key: str | None,
) -> LangChainAdapter:
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: LangChainAdapter = effective_client.create_adapter(
            "langchain",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
        return ad
    return _default_adapter()


def instrument_chain(
    chain: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Any:
    """Attach the agentegrity callback handler to a LangChain runnable."""
    return _resolve(profile, client, enforce, api_key).instrument_chain(chain)


def instrument_graph(
    graph: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Any:
    """Attach the agentegrity callback handler to a LangGraph compiled graph."""
    return _resolve(profile, client, enforce, api_key).instrument_graph(graph)


def callback_handler(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> Any:
    """Return a raw ``BaseCallbackHandler`` for users who register manually."""
    return _resolve(profile, client, enforce, api_key).create_callback_handler()


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "langchain",
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
