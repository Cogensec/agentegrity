"""
Agentegrity Client - high-level convenience wrapper for the most
common evaluation patterns.

This is the recommended entry point for most users.
"""

from __future__ import annotations

import importlib
import time
from typing import Any

from agentegrity.core._telemetry_props import (
    adapter_shape,
    profile_shape,
    record_shape,
    score_shape,
)
from agentegrity.core.attestation import AttestationRecord, build_attestation_record
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore, PropertyWeights
from agentegrity.core.monitor import IntegrityMonitor, ViolationAction
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier
from agentegrity.core.telemetry import scoped_telemetry, telemetry_capture, telemetry_tag
from agentegrity.layers.adversarial import AdversarialLayer
from agentegrity.layers.cortical import CorticalLayer
from agentegrity.layers.governance import GovernanceLayer
from agentegrity.layers.recovery import RecoveryLayer

# Maps adapter name → (module path, class name). Lazy: the framework
# module is imported only when the adapter is actually requested, so
# users who never call create_adapter("crewai", ...) never pay the
# crewai import cost. New adapters: add one line here.
_ADAPTER_REGISTRY: dict[str, tuple[str, str]] = {
    "claude": ("agentegrity.adapters.claude", "ClaudeAdapter"),
    "langchain": ("agentegrity.adapters.langchain", "LangChainAdapter"),
    "openai_agents": ("agentegrity.adapters.openai_agents", "OpenAIAgentsAdapter"),
    "crewai": ("agentegrity.adapters.crewai", "CrewAIAdapter"),
    "google_adk": ("agentegrity.adapters.google_adk", "GoogleADKAdapter"),
    "autogen": ("agentegrity.adapters.autogen", "AutoGenAdapter"),
    "agno": ("agentegrity.adapters.agno", "AgnoAdapter"),
    "bedrock_agents": ("agentegrity.adapters.bedrock_agents", "BedrockAgentsAdapter"),
}


class AgentegrityClient:
    """
    High-level client for the Agentegrity Framework.

    Provides a simplified interface for the most common patterns:
    creating agent profiles, running evaluations, and setting up
    runtime monitoring.

    Parameters
    ----------
    policy_set : str
        Governance policy set. Default "enterprise-default".
    coherence_threshold : float
        Adversarial coherence threshold. Default 0.70.
    drift_tolerance : float
        Behavioral drift tolerance. Default 0.15.
    weights : PropertyWeights, optional
        Custom property weights for composite scoring.

    Examples
    --------
    >>> client = AgentegrityClient()
    >>> profile = client.create_profile(
    ...     name="my-agent",
    ...     agent_type="tool_using",
    ...     capabilities=["tool_use", "memory_access"],
    ...     risk_tier="high"
    ... )
    >>> result = client.evaluate(profile)
    >>> print(result.composite)
    0.92
    """

    def __init__(
        self,
        policy_set: str = "enterprise-default",
        coherence_threshold: float = 0.70,
        drift_tolerance: float = 0.15,
        weights: PropertyWeights | None = None,
    ):
        self._adversarial = AdversarialLayer(coherence_threshold=coherence_threshold)
        self._cortical = CorticalLayer(drift_tolerance=drift_tolerance)
        self._governance = GovernanceLayer(policy_set=policy_set)
        self._recovery = RecoveryLayer()

        self._evaluator = IntegrityEvaluator(
            layers=[self._adversarial, self._cortical, self._governance, self._recovery],
            weights=weights,
        )

    def create_profile(
        self,
        name: str,
        agent_type: str = "tool_using",
        capabilities: list[str] | None = None,
        deployment_context: str = "cloud",
        risk_tier: str = "medium",
        **kwargs: Any,
    ) -> AgentProfile:
        """
        Create an agent profile with sensible defaults.

        Parameters
        ----------
        name : str
            Human-readable agent name.
        agent_type : str
            One of: conversational, tool_using, autonomous, multi_agent, embodied
        capabilities : list[str]
            Agent capabilities. Defaults to ["tool_use"].
        deployment_context : str
            One of: cloud, edge, hybrid, multi_agent, federated, physical
        risk_tier : str
            One of: low, medium, high, critical
        """
        return AgentProfile(
            name=name,
            agent_type=AgentType(agent_type),
            capabilities=capabilities or ["tool_use"],
            deployment_context=DeploymentContext(deployment_context),
            risk_tier=RiskTier(risk_tier),
            **kwargs,
        )

    @scoped_telemetry
    def evaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> IntegrityScore:
        """
        Run a full integrity evaluation across all four layers.

        Parameters
        ----------
        profile : AgentProfile
            The agent to evaluate.
        context : dict, optional
            Runtime context (current action, inputs, memory state, etc.)

        Returns
        -------
        IntegrityScore
            Composite score with per-property and per-layer breakdown.
        """
        telemetry_tag("component", "client")
        telemetry_tag("operation", "evaluate")
        telemetry_capture("client_evaluate_started", properties=profile_shape(profile))
        started = time.perf_counter()
        score = self._evaluator.evaluate(profile, context)
        telemetry_capture(
            "client_evaluate_finished",
            properties={
                **profile_shape(profile),
                **score_shape(score),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return score

    def monitor(
        self,
        profile: AgentProfile,
        threshold: float = 0.70,
        on_violation: str = "alert",
    ) -> IntegrityMonitor:
        """
        Create an IntegrityMonitor for continuous runtime monitoring.

        Parameters
        ----------
        profile : AgentProfile
            The agent to monitor.
        threshold : float
            Minimum acceptable composite score.
        on_violation : str
            Action on violation: "log", "alert", "block", or "escalate".

        Returns
        -------
        IntegrityMonitor
            A monitor instance with a `.guard` decorator.
        """
        return IntegrityMonitor(
            profile=profile,
            evaluator=self._evaluator,
            threshold=threshold,
            on_violation=ViolationAction(on_violation),
        )

    @scoped_telemetry
    def attest(
        self,
        profile: AgentProfile,
        score: IntegrityScore,
    ) -> AttestationRecord:
        """
        Generate an attestation record for an integrity evaluation.

        Parameters
        ----------
        profile : AgentProfile
            The evaluated agent.
        score : IntegrityScore
            The evaluation result.

        Returns
        -------
        AttestationRecord
            An unsigned attestation record. Call .sign() with a
            private key to produce a verifiable attestation.
        """
        telemetry_tag("component", "client")
        telemetry_tag("operation", "attest")
        record = build_attestation_record(profile, score)
        telemetry_capture("client_attest", properties=record_shape(record))
        return record

    @scoped_telemetry
    def create_adapter(
        self,
        name: str,
        profile: AgentProfile,
        *,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> Any:
        """Create a framework adapter wired to this client's evaluator.

        Parameters
        ----------
        name : str
            Adapter name. One of ``_ADAPTER_REGISTRY``'s keys (e.g.
            ``"claude"``, ``"langchain"``, ``"openai_agents"``,
            ``"crewai"``, ``"google_adk"``).
        profile : AgentProfile
            The agent profile the adapter will evaluate.
        enforce : bool
            If True, adapters that support enforcement will block tool
            calls when the integrity score's action is ``"block"``.
            Observation-only adapters (OTel-based) log this flag but
            cannot enforce; they will emit a runtime warning.
        api_key : str, optional
            Forwarded to the adapter for framework-specific auth needs.
        """
        try:
            module_path, class_name = _ADAPTER_REGISTRY[name]
        except KeyError:
            valid = ", ".join(sorted(_ADAPTER_REGISTRY))
            raise ValueError(
                f"Unknown adapter '{name}'. Valid: {valid}"
            ) from None
        telemetry_tag("component", "client")
        telemetry_tag("operation", "create_adapter")
        try:
            module = importlib.import_module(module_path)
            adapter_cls = getattr(module, class_name)
            adapter = adapter_cls(
                profile=profile,
                evaluator=self._evaluator,
                enforce=enforce,
                api_key=api_key,
            )
        except ImportError:
            # Framework not installed — still the highest-value signal we have.
            telemetry_capture(
                "adapter_created",
                properties={**adapter_shape(name), "framework_available": False},
            )
            raise
        telemetry_capture(
            "adapter_created",
            properties={**adapter_shape(name), "framework_available": True},
        )
        return adapter

    @property
    def evaluator(self) -> IntegrityEvaluator:
        return self._evaluator

    @property
    def adversarial_layer(self) -> AdversarialLayer:
        return self._adversarial

    @property
    def cortical_layer(self) -> CorticalLayer:
        return self._cortical

    @property
    def governance_layer(self) -> GovernanceLayer:
        return self._governance

    def __repr__(self) -> str:
        return f"AgentegrityClient(evaluator={self._evaluator})"
