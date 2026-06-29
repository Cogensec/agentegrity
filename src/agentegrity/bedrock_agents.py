"""Zero-config Bedrock Agents instrumentation.

Two surfaces, one adapter::

    # Strands SDK (real enforcement available)
    from agentegrity.bedrock_agents import instrument_strands, report
    agent = instrument_strands(agent)
    agent("...")
    print(report())

    # boto3 bedrock-agent-runtime (observation-only)
    import boto3
    from agentegrity.bedrock_agents import wrap_client, report
    client = wrap_client(boto3.client("bedrock-agent-runtime"))
    response = client.invoke_agent(agentId="...", agentAliasId="...",
                                   sessionId="...", inputText="...")
    for event in response["completion"]:
        ...
    print(report())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from strands.agent import Agent as StrandsAgent

    from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter

__all__ = [
    "adapter",
    "instrument_strands",
    "register_exporter",
    "report",
    "reset",
    "wrap_client",
]


_default: BedrockAgentsAdapter | None = None


def _default_adapter() -> BedrockAgentsAdapter:
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter(
            "bedrock_agents", profile=AgentProfile.default()
        )
    return _default


def adapter() -> BedrockAgentsAdapter:
    return _default_adapter()


def _resolve(
    profile: AgentProfile | None,
    client: AgentegrityClient | None,
    enforce: bool,
    api_key: str | None,
) -> BedrockAgentsAdapter:
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: BedrockAgentsAdapter = effective_client.create_adapter(
            "bedrock_agents",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
        return ad
    return _default_adapter()


def instrument_strands(
    agent: StrandsAgent,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> StrandsAgent:
    """Register agentegrity hooks on a Strands Agent. Returns the agent."""
    ad = _resolve(profile, client, enforce, api_key)
    return ad.instrument_strands(agent)


def wrap_client(
    boto3_client: Any,
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
    force_trace: bool = True,
) -> Any:
    """Wrap a bedrock-agent-runtime boto3 client's invoke_agent.

    Returns the same client with `invoke_agent` replaced. Trace events are
    consumed and re-yielded so the caller's streaming consumption is
    unchanged.
    """
    ad = _resolve(profile, client, enforce, api_key)
    return ad.wrap_client(boto3_client, force_trace=force_trace)


def report() -> dict[str, Any]:
    global _default
    if _default is None:
        return {
            "adapter": "bedrock_agents",
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
