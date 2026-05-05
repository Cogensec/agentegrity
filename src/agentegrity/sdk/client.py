"""
Agentegrity Client - high-level convenience wrapper for the most
common evaluation patterns.

This is the recommended entry point for most users.
"""

from __future__ import annotations

from typing import Any

from agentegrity.core.attestation import AttestationRecord, Evidence
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore, PropertyWeights
from agentegrity.core.monitor import IntegrityMonitor, ViolationAction
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier
from agentegrity.layers.adversarial import AdversarialLayer
from agentegrity.layers.cortical import CorticalLayer
from agentegrity.layers.governance import GovernanceLayer
from agentegrity.layers.recovery import RecoveryLayer


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
        return self._evaluator.evaluate(profile, context)

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
        return AttestationRecord(
            agent_id=profile.agent_id,
            integrity_score=score.to_dict(),
            layer_states={r.layer_name: r.to_dict() for r in score.layer_results},
            evidence=[
                Evidence(
                    evidence_type="layer_result",
                    source=r.layer_name,
                    content_hash=str(hash(str(r.to_dict()))),
                    summary=f"{r.layer_name}: {r.score:.3f} ({r.action})",
                )
                for r in score.layer_results
            ],
        )

    def create_claude_adapter(
        self,
        profile: AgentProfile,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> Any:
        """Create a ClaudeAdapter wired to this client's evaluator."""
        from agentegrity.adapters.claude import ClaudeAdapter

        return ClaudeAdapter(
            profile=profile,
            evaluator=self._evaluator,
            enforce=enforce,
            api_key=api_key,
        )

    def create_langchain_adapter(
        self,
        profile: AgentProfile,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> Any:
        """Create a LangChainAdapter (also covers LangGraph) wired to this evaluator."""
        from agentegrity.adapters.langchain import LangChainAdapter

        return LangChainAdapter(
            profile=profile,
            evaluator=self._evaluator,
            enforce=enforce,
            api_key=api_key,
        )

    def create_openai_agents_adapter(
        self,
        profile: AgentProfile,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> Any:
        """Create an OpenAIAgentsAdapter wired to this evaluator."""
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        return OpenAIAgentsAdapter(
            profile=profile,
            evaluator=self._evaluator,
            enforce=enforce,
            api_key=api_key,
        )

    def create_crewai_adapter(
        self,
        profile: AgentProfile,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> Any:
        """Create a CrewAIAdapter wired to this evaluator."""
        from agentegrity.adapters.crewai import CrewAIAdapter

        return CrewAIAdapter(
            profile=profile,
            evaluator=self._evaluator,
            enforce=enforce,
            api_key=api_key,
        )

    def create_google_adk_adapter(
        self,
        profile: AgentProfile,
        enforce: bool = False,
        api_key: str | None = None,
    ) -> Any:
        """Create a GoogleADKAdapter wired to this evaluator."""
        from agentegrity.adapters.google_adk import GoogleADKAdapter

        return GoogleADKAdapter(
            profile=profile,
            evaluator=self._evaluator,
            enforce=enforce,
            api_key=api_key,
        )

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
