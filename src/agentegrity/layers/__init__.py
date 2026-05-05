"""Integrity layers shipped with the framework.

The default integrity pipeline composes four layers:

    AdversarialLayer  → self-defense (coherence under adversarial pressure)
    CorticalLayer     → self-stability (reasoning, memory, drift)
    GovernanceLayer   → policy enforcement, human oversight, audit
    RecoveryLayer     → self-recovery (compromise detection, continuity)

Use :func:`default_layers` to construct the full default pipeline. Both
:class:`agentegrity.AgentegrityClient` and :class:`agentegrity.adapters.base._BaseAdapter`
delegate to it so every "zero-config" entry point evaluates the same
four layers.
"""

from agentegrity.core.evaluator import Layer
from agentegrity.layers.adversarial import AdversarialLayer
from agentegrity.layers.baseline_store import (
    BaselineStore,
    FileBaselineStore,
    InMemoryBaselineStore,
    SqliteBaselineStore,
)
from agentegrity.layers.checkpoint import (
    Checkpoint,
    CheckpointSnapshot,
    FileCheckpoint,
    InMemoryCheckpoint,
    SqliteCheckpoint,
)
from agentegrity.layers.cortical import CorticalLayer
from agentegrity.layers.governance import GovernanceLayer
from agentegrity.layers.recovery import RecoveryLayer


def default_layers(
    *,
    coherence_threshold: float = 0.70,
    drift_tolerance: float = 0.15,
    policy_set: str = "enterprise-default",
    prefer_llm: bool = False,
    api_key: str | None = None,
) -> list[Layer]:
    """Build the default four-layer integrity pipeline.

    Parameters mirror the keyword arguments accepted by the underlying
    layer constructors. Returned as ``list[Layer]`` so it can be passed
    straight into :class:`IntegrityEvaluator`.

    Parameters
    ----------
    prefer_llm : bool
        When True, swap the pattern-based :class:`CorticalLayer` for
        :class:`CorticalLLMLayer` — the LLM-backed sibling that adds
        Claude-driven semantic checks on the async evaluation path.
        Requires ``pip install agentegrity[llm]``; raises
        :class:`ImportError` if ``anthropic`` isn't installed. Default
        ``False`` so callers don't accidentally pay LLM latency / API
        spend on every evaluation.
    api_key : str, optional
        Anthropic API key for the LLM checkers. When omitted, the
        underlying checkers fall back to ``ANTHROPIC_API_KEY`` from the
        environment and fail open if it's also unset.
    """
    cortical: Layer
    if prefer_llm:
        # cortical_llm imports anthropic lazily on the first LLM call,
        # so a missing dependency wouldn't surface here without a
        # second check. Probe for anthropic directly so the operator
        # gets a clear error at construction time rather than a
        # confusing runtime failure on the first evaluate().
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "default_layers(prefer_llm=True) requires the 'anthropic' "
                "package. Install with: pip install agentegrity[llm]"
            ) from exc
        from agentegrity.layers.cortical_llm import CorticalLLMLayer
        cortical = CorticalLLMLayer(
            drift_tolerance=drift_tolerance, api_key=api_key
        )
    else:
        cortical = CorticalLayer(drift_tolerance=drift_tolerance)

    return [
        AdversarialLayer(coherence_threshold=coherence_threshold),
        cortical,
        GovernanceLayer(policy_set=policy_set),
        RecoveryLayer(),
    ]


__all__ = [
    "AdversarialLayer",
    "CorticalLayer",
    "GovernanceLayer",
    "RecoveryLayer",
    "default_layers",
    # Checkpoint API (recovery)
    "Checkpoint",
    "CheckpointSnapshot",
    "InMemoryCheckpoint",
    "FileCheckpoint",
    "SqliteCheckpoint",
    # BaselineStore API (cortical)
    "BaselineStore",
    "InMemoryBaselineStore",
    "FileBaselineStore",
    "SqliteBaselineStore",
]
