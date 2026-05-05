"""
Adversarial Layer - the outermost integrity defense.

Continuously tests and validates the agent's resilience to attack
across all input channels. Produces adversarial coherence scores
and threat assessments.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Pattern

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
class DetectorPattern:
    """A single regex-backed threat detection pattern.

    Patterns are organized by ``threat_type`` (a coarse taxonomy:
    prompt_injection, jailbreak, role_confusion, data_exfiltration,
    system_prompt_extraction, prompt_obfuscation, tool_manipulation).
    Each pattern carries its own ``severity`` and ``confidence`` so the
    aggregate :class:`ThreatAssessment` for a channel can summarise the
    most severe match without losing per-pattern provenance.

    The ``pattern`` is compiled lazily on first use; pass either a raw
    string (``re.IGNORECASE`` is applied automatically) or a pre-compiled
    :class:`re.Pattern` if you need different flags.
    """

    name: str
    pattern: str | Pattern[str]
    threat_type: str
    severity: float = 0.75
    confidence: float = 0.60
    description: str = ""
    flags: int = re.IGNORECASE

    def __post_init__(self) -> None:
        if isinstance(self.pattern, str):
            self._compiled: Pattern[str] = re.compile(self.pattern, self.flags)
        else:
            self._compiled = self.pattern
        if not 0.0 <= self.severity <= 1.0:
            raise ValueError(
                f"DetectorPattern.severity must be in [0,1], got {self.severity}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"DetectorPattern.confidence must be in [0,1], got {self.confidence}"
            )

    def search(self, text: str) -> str | None:
        """Return the matched substring, or None."""
        m = self._compiled.search(text)
        return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Default detector taxonomy.
#
# This is the *reference* pattern library. It is intentionally conservative
# (false positives are worse than false negatives in a measurement library)
# and far from exhaustive. Production deployments should layer in
# domain-specific patterns via the ``extra_patterns=`` constructor arg, or
# replace it wholesale via ``patterns=``. v0.6.0 will add an embedding-based
# semantic detector + an LLM-backed classifier as additional defence layers.
#
# Severity calibration:
#   1.00  catastrophic (immediate exfiltration of secrets)
#   0.85  high (jailbreak / role override)
#   0.75  medium (prompt injection, system prompt fishing)
#   0.50  low (suspicious phrasing, weak signal)
# Confidence calibration:
#   0.85+ regex captures attack-specific phrasing rarely seen in benign text
#   0.60  regex matches both attack and ambiguous benign text
#   0.40  weak signal that depends on combination with other matches
# ---------------------------------------------------------------------------

_PROMPT_INJECTION_PATTERNS: list[DetectorPattern] = [
    DetectorPattern(
        name="ignore_previous",
        pattern=r"\bignore\s+(all\s+|any\s+|the\s+|every\s+)?(previous|prior|above|preceding)\s+(instructions?|prompts?|rules?|directives?|context)\b",
        threat_type="prompt_injection",
        severity=0.80,
        confidence=0.85,
        description="Imperative to discard prior instructions",
    ),
    DetectorPattern(
        name="disregard_above",
        pattern=r"\b(disregard|forget|delete|erase)\s+(everything|all|your)\s+(above|prior|previous|instructions?|context|memory)\b",
        threat_type="prompt_injection",
        severity=0.80,
        confidence=0.80,
        description="Imperative to forget prior context",
    ),
    DetectorPattern(
        name="new_instructions",
        pattern=r"\b(new|updated|revised|replacement)\s+(instructions?|directives?|rules?|orders?)\s*[:\-]",
        threat_type="prompt_injection",
        severity=0.70,
        confidence=0.70,
        description="Header introducing a replacement instruction set",
    ),
    DetectorPattern(
        name="override_directive",
        pattern=r"\boverride\s*[:\-]|^\s*override\b",
        threat_type="prompt_injection",
        severity=0.70,
        confidence=0.55,
        description="Override directive header",
    ),
    DetectorPattern(
        name="boundary_marker_injection",
        # Markers commonly used to fence in adversarial content: --- END ---,
        # ###SYSTEM###, [SYSTEM], etc.
        pattern=r"(\#{2,}\s*system\s*\#{2,}|\[\s*system\s*\]|---+\s*(end|begin|system)\s*---+)",
        threat_type="prompt_injection",
        severity=0.65,
        confidence=0.55,
        description="Fence/boundary marker often used to inject a fake system block",
    ),
]

_JAILBREAK_PATTERNS: list[DetectorPattern] = [
    DetectorPattern(
        name="dan_mode",
        pattern=r"\b(DAN|do\s+anything\s+now|jailbreak\s+mode|developer\s+mode|debug\s+mode)\b",
        threat_type="jailbreak",
        severity=0.85,
        confidence=0.75,
        description="Known jailbreak persona / mode invocation",
    ),
    DetectorPattern(
        name="evil_twin",
        pattern=r"\b(evil|opposite|unfiltered|unrestricted|uncensored)\s+(twin|version|self|mode|model|persona)\b",
        threat_type="jailbreak",
        severity=0.80,
        confidence=0.75,
        description="Persona-shift jailbreak phrasing",
    ),
    DetectorPattern(
        name="no_restrictions",
        pattern=r"\b(no|without|bypass(ing)?|remove|disable|skip)\s+(any\s+|all\s+|the\s+|every\s+)?(restrictions?|filters?|safety(\s+(rails?|guards?))?|guardrails?|guidelines?|ethic(s|al)?|content\s+polic(y|ies)|safeguards?)\b",
        threat_type="jailbreak",
        severity=0.80,
        confidence=0.70,
        description="Demand to operate without safety constraints",
    ),
    DetectorPattern(
        name="hypothetical_unsafe",
        pattern=r"\b(hypothetically|in\s+a\s+fictional\s+(world|scenario|story)|for\s+research\s+purposes)\b.*\b(how\s+to|step.?by.?step|explain|teach\s+me)\b",
        threat_type="jailbreak",
        severity=0.65,
        confidence=0.55,
        description="Fiction/hypothetical framing followed by capability extraction",
    ),
]

_ROLE_CONFUSION_PATTERNS: list[DetectorPattern] = [
    DetectorPattern(
        name="you_are_now",
        pattern=r"\byou\s+are\s+(now|actually|really)\s+(an?\s+)?(?!helpful)([\w\s\-]{1,40})",
        threat_type="role_confusion",
        severity=0.70,
        confidence=0.55,
        description="Imperative role reassignment",
    ),
    DetectorPattern(
        name="pretend_to_be",
        pattern=r"\b(pretend|act|roleplay|behave)\s+(to\s+be|as|like)\s+(an?\s+)?\w+",
        threat_type="role_confusion",
        severity=0.55,
        confidence=0.45,
        description="Roleplay invocation (often benign — confidence is low)",
    ),
    DetectorPattern(
        name="ignore_your_role",
        pattern=r"\b(ignore|forget|abandon|drop)\s+(your|the)\s+(\w+\s+)?(role|persona|character|assistant\s+role|previous\s+identity|identity)\b",
        threat_type="role_confusion",
        severity=0.85,
        confidence=0.80,
        description="Imperative to drop the assigned role",
    ),
]

_SYSTEM_PROMPT_EXTRACTION_PATTERNS: list[DetectorPattern] = [
    DetectorPattern(
        name="reveal_system_prompt",
        pattern=r"\b(reveal|show|print|display|tell|repeat|output)\s+(me\s+)?(your|the)\s+(system\s+prompt|initial\s+instructions?|hidden\s+instructions?|prompt|configuration|hidden\s+\w+)\b",
        threat_type="system_prompt_extraction",
        severity=0.75,
        confidence=0.85,
        description="Direct request for the system prompt",
    ),
    DetectorPattern(
        name="quote_above_verbatim",
        pattern=r"\b(quote|repeat|echo|print)\s+(everything|all|the\s+text)\s+(above|before|that\s+came\s+before)\b",
        threat_type="system_prompt_extraction",
        severity=0.65,
        confidence=0.70,
        description="Verbatim-echo request often used to extract system context",
    ),
    DetectorPattern(
        name="what_were_you_told",
        pattern=r"\b(what\s+(were|are)\s+you\s+(told|instructed|configured|programmed)|what\s+is\s+your\s+(prompt|configuration|system\s+message))\b",
        threat_type="system_prompt_extraction",
        severity=0.55,
        confidence=0.60,
        description="Question form aimed at extracting system instructions",
    ),
]

_DATA_EXFILTRATION_PATTERNS: list[DetectorPattern] = [
    DetectorPattern(
        name="exfil_to_url",
        pattern=r"\b(send|post|upload|transmit|exfiltrate|forward|leak|publish)\b[^\.]{0,80}\b(to|at|via)\s+(https?://|webhook|external|endpoint|server|attacker|my\s+server)",
        threat_type="data_exfiltration",
        severity=0.95,
        confidence=0.85,
        description="Imperative to send data to an external endpoint",
    ),
    DetectorPattern(
        name="dump_secrets",
        pattern=r"\b(dump|leak|expose|reveal|print\s+all)\b[^\.]{0,40}\b(secrets?|tokens?|api[\s_-]?keys?|credentials?|passwords?|env(ironment)?\s+variables?)\b",
        threat_type="data_exfiltration",
        severity=1.00,
        confidence=0.85,
        description="Imperative to dump secrets",
    ),
    DetectorPattern(
        name="bypass_redaction",
        pattern=r"\b(bypass|skip|disable|turn\s+off)\s+(redaction|pii\s+filter|data\s+protection|privacy\s+filter)\b",
        threat_type="data_exfiltration",
        severity=0.85,
        confidence=0.80,
        description="Demand to disable data-protection controls",
    ),
]

_PROMPT_OBFUSCATION_PATTERNS: list[DetectorPattern] = [
    DetectorPattern(
        name="base64_decode_hint",
        pattern=r"\b(decode|base64|b64)\b.*\b(then|and)\s+(execute|follow|run|interpret|act\s+on)\b",
        threat_type="prompt_obfuscation",
        severity=0.70,
        confidence=0.65,
        description="Imperative to decode base64 then execute",
    ),
    DetectorPattern(
        name="rot13_decode_hint",
        pattern=r"\brot\s*13\b.*\b(then|and)\s+(execute|follow|run|interpret)\b",
        threat_type="prompt_obfuscation",
        severity=0.60,
        confidence=0.60,
        description="Imperative to ROT-13 decode then execute",
    ),
    DetectorPattern(
        name="zero_width_chars",
        # Zero-width space, ZWNJ, ZWJ — often used to smuggle instructions
        # past human review. Match if the input has 3+ in a 50-char window.
        pattern=r"(?:[​-‍]{1}.{0,50}){3,}",
        threat_type="prompt_obfuscation",
        severity=0.50,
        confidence=0.50,
        description="Repeated zero-width characters (possible instruction smuggling)",
    ),
]


def default_detector_patterns() -> list[DetectorPattern]:
    """Return the canonical detector taxonomy (a fresh list per call).

    Callers can extend or filter this list and pass the result to
    :class:`AdversarialLayer` via the ``patterns=`` argument.
    """
    return [
        *_PROMPT_INJECTION_PATTERNS,
        *_JAILBREAK_PATTERNS,
        *_ROLE_CONFUSION_PATTERNS,
        *_SYSTEM_PROMPT_EXTRACTION_PATTERNS,
        *_DATA_EXFILTRATION_PATTERNS,
        *_PROMPT_OBFUSCATION_PATTERNS,
    ]


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
    patterns : list[DetectorPattern], optional
        Replace the default regex pattern taxonomy entirely. Use
        :func:`default_detector_patterns` as a starting point.
    extra_patterns : list[DetectorPattern], optional
        Append additional patterns to the default taxonomy. Useful for
        domain-specific rules without losing the built-in ones.
    """

    def __init__(
        self,
        coherence_threshold: float = 0.70,
        threat_detectors: list[Any] | None = None,
        block_on_critical: bool = True,
        patterns: list[DetectorPattern] | None = None,
        extra_patterns: list[DetectorPattern] | None = None,
    ):
        self.coherence_threshold = coherence_threshold
        self._custom_detectors = threat_detectors or []
        self.block_on_critical = block_on_critical
        if patterns is None:
            self._patterns = default_detector_patterns()
        else:
            self._patterns = list(patterns)
        if extra_patterns:
            self._patterns.extend(extra_patterns)

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

        Iterates the registered :class:`DetectorPattern` taxonomy against
        the prompt input and aggregates matches into one
        :class:`ThreatAssessment` per (channel, threat_type) pair. The
        aggregate severity is the maximum across matches and confidence
        is the maximum across matches; ``indicators`` lists the matched
        pattern names.

        Production deployments should register custom detector callables
        via ``threat_detectors=`` and/or extra regex patterns via
        ``extra_patterns=``.
        """
        threats: list[ThreatAssessment] = []

        # 1. Pattern-based scan over the prompt input
        input_data = context.get("input", "")
        if isinstance(input_data, str) and input_data:
            threats.extend(self._scan_text(input_data, channel="direct_prompt"))

        # 2. Pattern-based scan over memory reads (model-context attacks)
        for read in context.get("memory_reads", []) or []:
            content = read.get("content") if isinstance(read, dict) else None
            if isinstance(content, str) and content:
                threats.extend(self._scan_text(content, channel="memory_reads"))

        # 3. Pattern-based scan over tool outputs (model-in-the-middle attacks)
        for output in context.get("tool_outputs", []) or []:
            if isinstance(output, dict):
                if output.get("error"):
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
                content = output.get("content") or output.get("result")
                if isinstance(content, str) and content:
                    threats.extend(self._scan_text(content, channel="tool_responses"))

        return threats

    def _scan_text(self, text: str, channel: str) -> list[ThreatAssessment]:
        """Run every registered pattern against ``text`` and aggregate
        matches per ``threat_type``."""
        matches_by_type: dict[str, list[tuple[DetectorPattern, str]]] = {}
        for pattern in self._patterns:
            matched = pattern.search(text)
            if matched is not None:
                matches_by_type.setdefault(pattern.threat_type, []).append(
                    (pattern, matched)
                )

        aggregated: list[ThreatAssessment] = []
        for threat_type, hits in matches_by_type.items():
            top_pattern = max(hits, key=lambda h: h[0].severity * h[0].confidence)[0]
            severity = max(p.severity for p, _ in hits)
            confidence = max(p.confidence for p, _ in hits)
            aggregated.append(
                ThreatAssessment(
                    channel=channel,
                    threat_type=threat_type,
                    severity=severity,
                    confidence=confidence,
                    description=top_pattern.description
                    or f"{threat_type.replace('_', ' ')} pattern matched",
                    indicators=[p.name for p, _ in hits],
                )
            )
        return aggregated

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
