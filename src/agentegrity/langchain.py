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

    # create_agent, where a denied call reaches the model and the run continues:
    from agentegrity.langchain import middleware
    agent = create_agent(model, tools, middleware=[middleware(enforce=True)])

A callback handler can only stop a denied call by raising, which ends the run.
``middleware()`` and ``tool_wrappers()`` (for a LangGraph ``ToolNode``) hand the
model the denial instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.adapters.base import empty_summary
from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.langchain import LangChainAdapter

__all__ = [
    "adapter",
    "callback_handler",
    "instrument_chain",
    "instrument_graph",
    "middleware",
    "register_exporter",
    "report",
    "reset",
    "tool_wrappers",
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
    on_deny: str = "continue",
) -> LangChainAdapter:
    if profile is not None or client is not None or enforce or on_deny != "continue":
        effective_client = client or AgentegrityClient()
        ad: LangChainAdapter = effective_client.create_adapter(
            "langchain",
            profile=profile or AgentProfile.default(),
            enforce=enforce,
            on_deny=on_deny,
        )
        return ad
    return _default_adapter()


def middleware(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    on_deny: str = "continue",
) -> Any:
    """Return a ``create_agent`` middleware that gates every tool call."""
    return _resolve(profile, client, enforce, on_deny).middleware()


def tool_wrappers(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    on_deny: str = "continue",
) -> dict[str, Any]:
    """Return ``wrap_tool_call`` and ``awrap_tool_call`` for ``ToolNode(tools, **...)``."""
    return _resolve(profile, client, enforce, on_deny).tool_wrappers()


def instrument_chain(
    chain: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
) -> Any:
    """Attach the agentegrity callback handler to a LangChain runnable."""
    return _resolve(profile, client, enforce).instrument_chain(chain)


def instrument_graph(
    graph: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
) -> Any:
    """Attach the agentegrity callback handler to a LangGraph compiled graph."""
    return _resolve(profile, client, enforce).instrument_graph(graph)


def callback_handler(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
) -> Any:
    """Return a raw ``BaseCallbackHandler`` for users who register manually."""
    return _resolve(profile, client, enforce).create_callback_handler()


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return empty_summary("langchain")
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    """Register a :class:`SessionExporter` on the module-global adapter."""
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    global _default
    _default = None
