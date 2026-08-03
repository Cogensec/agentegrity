"""Two-line entry point: detect installed frameworks and instrument.

    import agentegrity

    runtime = agentegrity.init()          # detect + build adapters
    chain = runtime.instrument(my_chain)  # object-level frameworks
    ...
    agentegrity.shutdown()                # end sessions, flush exporters

``init()`` probes the environment for every framework in the adapter
registry, builds one adapter per detected framework on a shared
:class:`AgentegrityClient`, and auto-subscribes the frameworks that
attach globally (CrewAI's event bus). Frameworks that instrument a
specific object (LangChain runnables, Agno agents, Strands agents,
Google ADK agents) go through :meth:`AgentegrityRuntime.instrument`,
which dispatches on the object's class module. Hook-style frameworks
(Claude Agent SDK ``create_hooks()``, OpenAI Agents
``create_run_hooks()``) are reached via ``runtime.adapters[name]``.

This is convenience wiring only — every adapter here behaves exactly
as if constructed via :meth:`AgentegrityClient.create_adapter`.
"""

from __future__ import annotations

import importlib.util
import logging
import warnings
from dataclasses import dataclass, field
from typing import Any

from agentegrity.core.profile import AgentProfile
from agentegrity.sdk.client import _ADAPTER_REGISTRY, AgentegrityClient

logger = logging.getLogger("agentegrity.sdk.runtime")

# Adapter name → module names whose importability signals the framework
# is installed. Any one hit counts.
_FRAMEWORK_PROBES: dict[str, tuple[str, ...]] = {
    "claude": ("claude_agent_sdk",),
    "langchain": ("langchain_core",),
    "openai_agents": ("agents",),
    "crewai": ("crewai",),
    "google_adk": ("google.adk",),
    "autogen": ("autogen_agentchat", "autogen_core"),
    "agno": ("agno",),
    "bedrock_agents": ("strands",),
}

# Class-module prefix → (adapter name, adapter method). Checked in
# order; first prefix match wins. langgraph before langchain so
# compiled graphs get topology introspection.
_DISPATCH_TABLE: tuple[tuple[str, str, str], ...] = (
    ("langgraph", "langchain", "instrument_graph"),
    ("langchain_core", "langchain", "instrument_chain"),
    ("langchain_community", "langchain", "instrument_chain"),
    ("langchain", "langchain", "instrument_chain"),
    ("agno.team", "agno", "instrument_team"),
    ("agno", "agno", "instrument"),
    ("strands", "bedrock_agents", "instrument_strands"),
    ("google.adk", "google_adk", "instrument"),
    ("crewai", "crewai", "subscribe"),
)

# Frameworks whose adapters attach process-globally at init() time.
# CrewAI subscribes to the global event bus — purely additive.
# AutoGen is deliberately NOT here: its instrument() installs a global
# OTel TracerProvider, which must stay an explicit choice.
_AUTO_ATTACH = ("crewai",)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        # A broken parent package (or invalidated spec) means "not
        # usable", which is all the probe needs to know.
        return False


def detect_frameworks() -> list[str]:
    """Return the registry adapter names whose framework is importable,
    in registry order."""
    return [
        name
        for name, probes in _FRAMEWORK_PROBES.items()
        if any(_module_available(p) for p in probes)
    ]


@dataclass
class AgentegrityRuntime:
    """Handle returned by :func:`init` — one client, one profile, one
    adapter per detected framework."""

    client: AgentegrityClient
    profile: AgentProfile
    adapters: dict[str, Any] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    def instrument(self, target: Any) -> Any:
        """Instrument a framework object, dispatching on its class module.

        Supports LangChain runnables / LangGraph compiled graphs, Agno
        agents and teams, Strands (Bedrock) agents, Google ADK agents,
        and CrewAI crews. Raises TypeError for anything else, and
        RuntimeError when the object's framework wasn't initialised.
        """
        module = type(target).__module__ or ""
        for prefix, adapter_name, method in _DISPATCH_TABLE:
            if module == prefix or module.startswith(prefix + "."):
                adapter = self.adapters.get(adapter_name)
                if adapter is None:
                    raise RuntimeError(
                        f"Object from {module!r} needs the {adapter_name!r} "
                        f"adapter, which init() did not build "
                        f"(detected: {sorted(self.adapters)}). Pass "
                        f"frameworks=[{adapter_name!r}, ...] to init()."
                    )
                return getattr(adapter, method)(target)
        raise TypeError(
            f"Cannot infer a framework from {type(target).__qualname__} "
            f"(module {module!r}). Use the adapter API directly via "
            f"runtime.adapters[<name>]."
        )

    def report(self) -> dict[str, dict[str, Any]]:
        """Per-adapter session summaries, keyed by adapter name."""
        return {name: a.get_summary() for name, a in self.adapters.items()}

    def shutdown(self) -> None:
        """End every adapter session (fires exporters' on_session_end)."""
        for adapter in self.adapters.values():
            try:
                adapter.close()
            except Exception as exc:  # noqa: BLE001 — shutdown must not raise
                logger.warning("adapter close failed: %s", exc)


_runtime: AgentegrityRuntime | None = None


def init(
    *,
    name: str = "agent",
    frameworks: list[str] | None = None,
    enforce: bool = False,
    policy_set: str = "enterprise-default",
    profile: AgentProfile | None = None,
) -> AgentegrityRuntime:
    """Detect installed frameworks and build one adapter per framework.

    Parameters
    ----------
    name : str
        Name for the default :class:`AgentProfile`. Ignored when
        ``profile`` is passed.
    frameworks : list[str], optional
        Explicit adapter names to build instead of auto-detection.
        Must be keys of the adapter registry.
    enforce : bool
        Forwarded to every adapter. Observation-only adapters warn.
    policy_set : str
        Governance policy set for the shared client.
    profile : AgentProfile, optional
        Use this profile instead of a default one.

    Idempotent: a second call returns the existing runtime (with a
    warning) until :func:`shutdown` is called.
    """
    global _runtime
    if _runtime is not None:
        warnings.warn(
            "agentegrity is already initialised; returning the existing "
            "runtime. Call agentegrity.shutdown() first to reconfigure.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _runtime

    if frameworks is None:
        selected = detect_frameworks()
    else:
        unknown = sorted(set(frameworks) - set(_ADAPTER_REGISTRY))
        if unknown:
            valid = ", ".join(sorted(_ADAPTER_REGISTRY))
            raise ValueError(
                f"Unknown framework(s) {unknown}. Valid: {valid}"
            )
        selected = list(frameworks)

    client = AgentegrityClient(policy_set=policy_set)
    effective_profile = profile or AgentProfile.default(name=name)

    runtime = AgentegrityRuntime(client=client, profile=effective_profile)
    for framework in selected:
        try:
            adapter = client.create_adapter(
                framework, profile=effective_profile, enforce=enforce
            )
        except ImportError as exc:
            # Probe said installed but the adapter's own deps are
            # missing (e.g. [autogen] extra without opentelemetry-sdk).
            runtime.skipped[framework] = str(exc)
            logger.info("skipping %s adapter: %s", framework, exc)
            continue
        runtime.adapters[framework] = adapter
        if framework in _AUTO_ATTACH:
            adapter.subscribe()

    logger.info(
        "agentegrity initialised: adapters=%s skipped=%s enforce=%s",
        sorted(runtime.adapters),
        sorted(runtime.skipped),
        enforce,
    )
    _runtime = runtime
    return runtime


def shutdown() -> None:
    """End the global runtime's sessions and clear it. Safe to call
    when :func:`init` was never called."""
    global _runtime
    if _runtime is not None:
        _runtime.shutdown()
        _runtime = None


__all__ = [
    "AgentegrityRuntime",
    "detect_frameworks",
    "init",
    "shutdown",
]
