"""
Agentegrity Framework - The open standard for AI agent integrity.

Agentegrity defines what it means for an autonomous AI agent to be whole:
adversarially coherent, environmentally portable, and verifiably assured.
"""

__version__ = "0.5.3"

from agentegrity.adapters.base import FrameworkEvent, SessionExporter
from agentegrity.claude import hooks as claude_hooks
from agentegrity.claude import report as claude_report
from agentegrity.core.attestation import AttestationChain, AttestationRecord
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore, PropertyWeights
from agentegrity.core.monitor import IntegrityMonitor
from agentegrity.core.profile import AgentProfile, AgentType, DeploymentContext, RiskTier
from agentegrity.crewai import instrument as crewai_instrument
from agentegrity.crewai import report as crewai_report
from agentegrity.google_adk import instrument as google_adk_instrument
from agentegrity.google_adk import report as google_adk_report
from agentegrity.langchain import instrument_chain as langchain_instrument_chain
from agentegrity.langchain import instrument_graph as langchain_instrument_graph
from agentegrity.langchain import report as langchain_report
from agentegrity.layers import (
    AdversarialLayer,
    Checkpoint,
    CheckpointSnapshot,
    CorticalLayer,
    FileCheckpoint,
    GovernanceLayer,
    InMemoryCheckpoint,
    RecoveryLayer,
    SqliteCheckpoint,
    default_layers,
)
from agentegrity.openai_agents import report as openai_agents_report
from agentegrity.openai_agents import run_hooks as openai_agents_run_hooks
from agentegrity.sdk.client import AgentegrityClient

__all__ = [
    "AgentProfile",
    "AgentType",
    "DeploymentContext",
    "RiskTier",
    "IntegrityEvaluator",
    "IntegrityScore",
    "PropertyWeights",
    "AttestationRecord",
    "AttestationChain",
    "IntegrityMonitor",
    "AgentegrityClient",
    "FrameworkEvent",
    "SessionExporter",
    "AdversarialLayer",
    "CorticalLayer",
    "GovernanceLayer",
    "RecoveryLayer",
    "default_layers",
    "Checkpoint",
    "CheckpointSnapshot",
    "InMemoryCheckpoint",
    "FileCheckpoint",
    "SqliteCheckpoint",
    "claude_hooks",
    "claude_report",
    "langchain_instrument_chain",
    "langchain_instrument_graph",
    "langchain_report",
    "openai_agents_run_hooks",
    "openai_agents_report",
    "crewai_instrument",
    "crewai_report",
    "google_adk_instrument",
    "google_adk_report",
]
