"""
Zero-config Claude Agent SDK instrumentation.

This module is the fastest way to add agentegrity to an existing
Claude Agent SDK agent. For a first-run experience, call
``hooks()`` with no arguments and pass the result to
``ClaudeAgentOptions(hooks=...)``.

Example
-------
>>> from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
>>> from agentegrity.claude import hooks, report
>>>
>>> async with ClaudeSDKClient(
...     options=ClaudeAgentOptions(hooks=hooks())
... ) as sdk:
...     await sdk.query("Summarize the latest LLM safety papers")
>>> print(report())

``hooks()`` lazily constructs a process-global
:class:`~agentegrity.adapters.claude.ClaudeAdapter` backed by an
:class:`~agentegrity.sdk.client.AgentegrityClient` with default
three-layer configuration and an :meth:`AgentProfile.default`
profile. ``report()`` returns that adapter's current session
summary. Call ``reset()`` between sessions (or in tests) to
discard the global.

Power users who need explicit configuration can pass
``profile=``, ``client=``, ``enforce=``, or ``api_key=`` — in
that case ``hooks()`` still returns hook matchers but no global
state is touched, and ``report()`` will only see activity from
the default adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import AgentegrityClient

if TYPE_CHECKING:
    from agentegrity.adapters.claude import ClaudeAdapter

__all__ = ["hooks", "report", "reset", "adapter", "register_exporter"]


_default: ClaudeAdapter | None = None


def _default_adapter() -> ClaudeAdapter:
    """Return the module-global adapter, constructing it on first use."""
    global _default
    if _default is None:
        client = AgentegrityClient()
        _default = client.create_adapter("claude", profile=AgentProfile.default())
    return _default


def adapter() -> ClaudeAdapter:
    """Public accessor for the module-global adapter. Constructs on first use."""
    return _default_adapter()


def hooks(
    *,
    profile: AgentProfile | None = None,
    client: AgentegrityClient | None = None,
    enforce: bool = False,
    api_key: str | None = None,
) -> dict[str, list[Any]]:
    """Return Claude Agent SDK hook matchers wired to an agentegrity adapter.

    Zero-config: calling ``hooks()`` with no arguments lazily creates
    a process-global default adapter (``AgentProfile.default()`` + the
    standard three-layer ``AgentegrityClient``) and returns its hook
    matchers. Subsequent calls reuse the same adapter so that
    ``report()`` can summarize the whole session.

    Parameters
    ----------
    profile : AgentProfile, optional
        Override the default profile. If supplied, ``hooks()`` builds
        a *new* adapter — the module-global is not touched. Use this
        when you are running multiple independent evaluations in the
        same process.
    client : AgentegrityClient, optional
        Override the default client (e.g., custom thresholds, custom
        weights). Also triggers a new adapter.
    enforce : bool
        If True, the returned hooks will deny tool calls whose
        pre-check produces ``action == "block"`` or ``"escalate"``.
        Default False (measure-only).
    api_key : str, optional
        Anthropic API key for LLM-backed cortical checks. Falls back
        to ``ANTHROPIC_API_KEY``.

    Returns
    -------
    dict[str, list]
        A hook matcher dict ready for ``ClaudeAgentOptions(hooks=...)``.
    """
    if profile is not None or client is not None or enforce or api_key is not None:
        effective_client = client or AgentegrityClient()
        effective_profile = profile or AgentProfile.default()
        ad: ClaudeAdapter = effective_client.create_adapter(
            "claude",
            profile=effective_profile,
            enforce=enforce,
            api_key=api_key,
        )
    else:
        ad = _default_adapter()
    result: dict[str, list[Any]] = ad.create_hooks()
    return result


def report() -> dict[str, Any]:
    """Return the current session summary for the default adapter.

    Returns an empty-but-well-formed summary if ``hooks()`` has never
    been called — callers never have to guard against ``None``.
    """
    global _default
    if _default is None:
        return {
            "adapter": "claude",
            "agent_id": None,
            "evaluations": 0,
            "events": 0,
            "attestation_records": 0,
            "chain_hash_linked": True,
            "enforce_mode": False,
        }
    return _default.get_summary()


def register_exporter(exporter: Any) -> None:
    """Register a :class:`SessionExporter` on the module-global adapter.

    Lazily constructs the default adapter on first call so the exporter
    receives every event from the subsequent session.
    """
    _default_adapter().register_exporter(exporter)


def reset() -> None:
    """Discard the module-global adapter.

    Useful in tests or when starting a new logical session in the
    same process. Subsequent ``hooks()`` or ``report()`` calls will
    construct a fresh adapter on demand.
    """
    global _default
    _default = None
