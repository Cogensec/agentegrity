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

from importlib.util import find_spec

from agentegrity.core.evaluator import Layer
from agentegrity.layers.adversarial import (
    AdversarialLayer,
    ToolArgumentDetector,
    ToolCategories,
    ToolSequenceDetector,
)
from agentegrity.layers.adversarial_llm import AdversarialLLMLayer
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
from agentegrity.layers.cortical_llm import CorticalLLMLayer
from agentegrity.layers.governance import GovernanceLayer
from agentegrity.layers.recovery import RecoveryLayer


def default_layers(
    *,
    coherence_threshold: float = 0.70,
    drift_tolerance: float = 0.15,
    policy_set: str = "enterprise-default",
    prefer_llm: bool = False,
    api_key: str | None = None,
    baseline_store: BaselineStore | None = None,
) -> list[Layer]:
    """Build the default four-layer integrity pipeline.

    Parameters mirror the keyword arguments accepted by the underlying
    layer constructors. Returned as ``list[Layer]`` so it can be passed
    straight into :class:`IntegrityEvaluator`.

    Parameters
    ----------
    prefer_llm : bool
        When True, use :class:`AdversarialLLMLayer` and
        :class:`CorticalLLMLayer`, which add Claude-backed classification
        on the async path. Adapters run that path at every tool-call gate,
        so each gate waits on Claude for text it has not classified yet.
        Requires ``pip install agentegrity[llm]``; raises
        :class:`ImportError` if ``anthropic`` isn't installed. Default
        ``False`` so callers don't pay LLM latency or API spend by accident.
    api_key : str, optional
        Anthropic API key for both LLM layers. When omitted they read
        ``ANTHROPIC_API_KEY`` and fail open (pattern checks only) if it is
        also unset.
    baseline_store : BaselineStore, optional
        Persists the cortical layer's behavioral baseline so drift is
        measured against earlier sessions, not only this process.
    """
    if not prefer_llm:
        return [
            AdversarialLayer(coherence_threshold=coherence_threshold),
            CorticalLayer(drift_tolerance=drift_tolerance, baseline_store=baseline_store),
            GovernanceLayer(policy_set=policy_set),
            RecoveryLayer(),
        ]
    # The LLM layers import anthropic on their first call, so probe for it here
    # to fail at construction instead of failing open on every evaluation.
    if find_spec("anthropic") is None:
        raise ImportError(
            "default_layers(prefer_llm=True) requires the 'anthropic' "
            "package. Install with: pip install agentegrity[llm]"
        )
    return [
        AdversarialLLMLayer(coherence_threshold=coherence_threshold, api_key=api_key),
        CorticalLLMLayer(drift_tolerance=drift_tolerance, api_key=api_key,
                         baseline_store=baseline_store),
        GovernanceLayer(policy_set=policy_set),
        RecoveryLayer(),
    ]


__all__ = [
    "AdversarialLayer",
    "AdversarialLLMLayer",
    "ToolArgumentDetector",
    "ToolCategories",
    "ToolSequenceDetector",
    "CorticalLayer",
    "CorticalLLMLayer",
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
