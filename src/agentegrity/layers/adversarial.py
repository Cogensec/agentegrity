"""
Adversarial Layer - the outermost integrity defense.

Continuously tests and validates the agent's resilience to attack
across all input channels. Produces adversarial coherence scores
and threat assessments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agentegrity.core.evaluator import LayerResult
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier

logger = logging.getLogger("agentegrity.adversarial")


@dataclass
class ThreatAssessment:
    """Assessment of a single threat signal."""

    channel: str
    threat_type: str  # prompt_injection | goal_hijacking | data_exfiltration | ...
    severity: float  # 0.0 - 1.0
    confidence: float  # 0.0 - 1.0
    description: str
    indicators: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "threat_type": self.threat_type,
            "severity": self.severity,
            "confidence": self.confidence,
            "description": self.description,
            "indicators": self.indicators,
        }


@dataclass
class AttackSurfaceMap:
    """Map of an agent's attack surface based on its capabilities."""

    channels: list[str]
    tool_interfaces: list[str]
    memory_surfaces: list[str]
    peer_interfaces: list[str]
    total_surface_area: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "channels": self.channels,
            "tool_interfaces": self.tool_interfaces,
            "memory_surfaces": self.memory_surfaces,
            "peer_interfaces": self.peer_interfaces,
            "total_surface_area": self.total_surface_area,
        }


class AdversarialLayer:
    """
    The adversarial layer evaluates an agent's resilience to attack.

    It maps the agent's attack surface, detects active threats across
    input channels, and computes an adversarial coherence score that
    measures how well the agent maintains consistent behavior under
    adversarial pressure.

    Parameters
    ----------
    coherence_threshold : float
        Minimum adversarial coherence score for the layer to pass.
        Default 0.70.
    threat_detectors : list[callable], optional
        Custom threat detection functions. Each receives
        (profile, context) and returns a list of ThreatAssessments.
    block_on_critical : bool
        If True, block on any threat with severity >= 0.90.
        Default True.
    """

    def __init__(
        self,
        coherence_threshold: float = 0.70,
        threat_detectors: list[Any] | None = None,
        block_on_critical: bool = True,
    ):
        self.coherence_threshold = coherence_threshold
        self._custom_detectors = threat_detectors or []
        self.block_on_critical = block_on_critical

    @property
    def name(self) -> str:
        return "adversarial"

    def evaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        """
        Evaluate the agent's adversarial integrity.

        1. Map the attack surface
        2. Run threat detection across channels
        3. Compute adversarial coherence score
        4. Determine pass/fail and action
        """
        ctx = context or {}
        threats: list[ThreatAssessment] = []

        # Step 1: Map attack surface
        surface = self.map_attack_surface(profile)

        # Step 2: Run built-in threat detection
        threats.extend(self._detect_channel_threats(profile, ctx))

        # Step 3: Run custom detectors
        for detector in self._custom_detectors:
            try:
                custom_threats = detector(profile, ctx)
                if custom_threats:
                    threats.extend(custom_threats)
            except Exception as exc:
                logger.warning("Custom detector failed: %s", exc, exc_info=True)

        # Step 4: Compute coherence score
        coherence_score = self._compute_coherence(profile, threats, ctx)

        # Step 5: Determine action
        max_severity = max((t.severity for t in threats), default=0.0)
        if self.block_on_critical and max_severity >= 0.90:
            action = "block"
            passed = False
        elif coherence_score < self.coherence_threshold:
            action = "alert"
            passed = False
        elif threats:
            action = "alert" if max_severity >= 0.50 else "pass"
            passed = coherence_score >= self.coherence_threshold
        else:
            action = "pass"
            passed = True

        return LayerResult(
            layer_name=self.name,
            score=coherence_score,
            passed=passed,
            action=action,
            details={
                "coherence_score": coherence_score,
                "coherence_threshold": self.coherence_threshold,
                "threat_count": len(threats),
                "max_threat_severity": max_severity,
                "threats": [t.to_dict() for t in threats],
                "attack_surface": surface.to_dict(),
                "channels_evaluated": len(surface.channels),
            },
        )

    async def aevaluate(
        self,
        profile: AgentProfile,
        context: dict[str, Any] | None = None,
    ) -> LayerResult:
        """Async wrapper around evaluate for use with AsyncIntegrityEvaluator."""
        return self.evaluate(profile, context)

    def map_attack_surface(self, profile: AgentProfile) -> AttackSurfaceMap:
        """
        Enumerate the agent's attack surface based on its profile.

        The attack surface is the set of all channels through which
        adversarial input can reach the agent.
        """
        channels = profile.input_channels
        tool_interfaces = []
        memory_surfaces = []
        peer_interfaces = []

        if profile.has_capability("tool_use"):
            tool_interfaces = ["tool_call_responses", "tool_error_messages"]
        if profile.has_capability("memory_access"):
            memory_surfaces = ["vector_store_reads", "context_window", "rag_retrievals"]
        if profile.has_capability("multi_agent_comm"):
            peer_interfaces = ["peer_messages", "shared_memory", "broadcast_channels"]

        total = len(channels) + len(tool_interfaces) + len(memory_surfaces) + len(peer_interfaces)

        return AttackSurfaceMap(
            channels=channels,
            tool_interfaces=tool_interfaces,
            memory_surfaces=memory_surfaces,
            peer_interfaces=peer_interfaces,
            total_surface_area=total,
        )

    def _detect_channel_threats(
        self,
        profile: AgentProfile,
        context: dict[str, Any],
    ) -> list[ThreatAssessment]:
        """
        Built-in threat detection across input channels.

        This is the reference implementation. Production deployments
        should register custom detectors with domain-specific logic.
        """
        threats = []

        # Check for known threat indicators in context
        input_data = context.get("input", "")
        if isinstance(input_data, str):
            # Basic prompt injection patterns (reference implementation)
            injection_indicators = [
                "ignore previous instructions",
                "ignore all prior",
                "system prompt",
                "you are now",
                "disregard above",
                "new instructions:",
                "override:",
            ]
            found = [ind for ind in injection_indicators if ind.lower() in input_data.lower()]
            if found:
                threats.append(
                    ThreatAssessment(
                        channel="direct_prompt",
                        threat_type="prompt_injection",
                        severity=0.75,
                        confidence=0.60,
                        description="Potential prompt injection detected in direct input",
                        indicators=found,
                    )
                )

        # Check tool response integrity
        tool_outputs = context.get("tool_outputs", [])
        for output in tool_outputs:
            if isinstance(output, dict) and output.get("error"):
                threats.append(
                    ThreatAssessment(
                        channel="tool_responses",
                        threat_type="tool_manipulation",
                        severity=0.30,
                        confidence=0.40,
                        description="Tool returned error — verify tool integrity",
                        indicators=[str(output.get("error"))],
                    )
                )

        return threats

    def _compute_coherence(
        self,
        profile: AgentProfile,
        threats: list[ThreatAssessment],
        context: dict[str, Any],
    ) -> float:
        """
        Compute the adversarial coherence score.

        The score starts at 1.0 and is degraded by detected threats,
        weighted by severity and confidence.
        """
        score = 1.0

        for threat in threats:
            # Each threat reduces coherence by severity * confidence
            impact = threat.severity * threat.confidence * 0.5
            score -= impact

        # Clamp to [0.0, 1.0]
        return max(0.0, min(1.0, round(score, 4)))

    def detect(
        self,
        input_data: Any,
        channel: str,
        profile: AgentProfile | None = None,
    ) -> ThreatAssessment | None:
        """
        Standalone threat detection for a single input on a specific channel.
        Useful for inline threat checks outside the full evaluation pipeline.
        """
        context = {"input": input_data, "channel": channel}
        threats = self._detect_channel_threats(
            profile or AgentProfile(
                agent_type=AgentType.CONVERSATIONAL,
                capabilities=[],
                deployment_context=DeploymentContext.CLOUD,
                risk_tier=RiskTier.MEDIUM,
            ),
            context,
        )
        return threats[0] if threats else None

    def __repr__(self) -> str:
        return (
            f"AdversarialLayer(threshold={self.coherence_threshold}, "
            f"detectors={1 + len(self._custom_detectors)})"
        )
