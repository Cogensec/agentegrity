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
) -> list[Layer]:
    """Build the default four-layer integrity pipeline.

    Parameters mirror the keyword arguments accepted by the underlying
    layer constructors. Returned as ``list[Layer]`` so it can be passed
    straight into :class:`IntegrityEvaluator`.
    """
    return [
        AdversarialLayer(coherence_threshold=coherence_threshold),
        CorticalLayer(drift_tolerance=drift_tolerance),
        GovernanceLayer(policy_set=policy_set),
        RecoveryLayer(),
    ]


__all__ = [
    "AdversarialLayer",
    "CorticalLayer",
    "GovernanceLayer",
    "RecoveryLayer",
    "default_layers",
    # Checkpoint API
    "Checkpoint",
    "CheckpointSnapshot",
    "InMemoryCheckpoint",
    "FileCheckpoint",
    "SqliteCheckpoint",
]
