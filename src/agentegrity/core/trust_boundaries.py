"""Provenance-aware trust boundary checks for high-impact agent tool inputs.

The host, not the model, must identify which upstream sources influenced each
protected argument. Labels and provenance are *claims supplied by trusted host
instrumentation*, not proof obtained by inspecting untrusted text. Do not build
these structures from prompt, RAG, MCP, or tool response metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType


class SourceTrust(str, Enum):
    """Authority assigned by the trusted host, never by source content."""

    AUTHORITY = "authority"
    DATA_ONLY = "data_only"


class SourceChannel(str, Enum):
    OPERATOR_POLICY = "operator_policy"
    VERIFIED_USER_REQUEST = "verified_user_request"
    RETRIEVAL = "retrieval"
    TOOL_RESPONSE = "tool_response"
    PEER_AGENT = "peer_agent"
    MEMORY = "memory"


@dataclass(frozen=True)
class SourceLabel:
    source_id: str
    trust: SourceTrust
    channel: SourceChannel

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be nonempty")
        if not isinstance(self.trust, SourceTrust):
            raise TypeError("trust must be SourceTrust")
        if not isinstance(self.channel, SourceChannel):
            raise TypeError("channel must be SourceChannel")
        if (self.channel in {
            SourceChannel.RETRIEVAL,
            SourceChannel.TOOL_RESPONSE,
            SourceChannel.PEER_AGENT,
            SourceChannel.MEMORY,
        } and self.trust == SourceTrust.AUTHORITY):
            raise ValueError("low-trust channels cannot grant action authority")


def _freeze_origins(
    groups: Mapping[str, frozenset[str]],
) -> Mapping[str, frozenset[str]]:
    frozen: dict[str, frozenset[str]] = {}
    for field_name, origins in groups.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise ValueError("provenance field name must be nonempty")
        if not isinstance(origins, frozenset):
            raise TypeError("provenance origins must be frozenset[str]")
        if not all(isinstance(origin, str) and origin.strip() for origin in origins):
            raise ValueError("source references must be nonempty strings")
        frozen[field_name] = frozenset(origins)
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class ActionProvenance:
    """Host-produced source lineage keyed by *exact* tool argument name.

    `control_origins` identifies who authorized each protected parameter.
    `data_origins` records source material used in the same parameter or in
    ordinary payload fields. A protected field contaminated by data-only
    sources is denied even if a trusted control origin is also claimed.
    """

    control_origins: Mapping[str, frozenset[str]]
    data_origins: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "control_origins", _freeze_origins(self.control_origins))
        object.__setattr__(self, "data_origins", _freeze_origins(self.data_origins))


@dataclass(frozen=True)
class BoundaryVerdict:
    allowed: bool
    reason: str
    field_name: str | None = None


class TrustBoundaryPolicy:
    """Explicit host-configured policy for authority-bearing tool arguments.

    All tools used with this policy must have an entry in `protected_fields`.
    Empty sets deliberately opt a specific tool out of argument provenance
    checks; this is suitable only when trusted tool design ensures that no
    argument can grant permissions or redirect effects.
    """

    def __init__(
        self,
        sources: Mapping[str, SourceLabel],
        protected_fields: Mapping[str, frozenset[str]],
    ) -> None:
        source_snapshot: dict[str, SourceLabel] = {}
        for key, label in sources.items():
            if not isinstance(label, SourceLabel) or key != label.source_id:
                raise ValueError("source registry IDs must match SourceLabel.source_id")
            source_snapshot[key] = label
        fields_snapshot: dict[str, frozenset[str]] = {}
        for tool, names in protected_fields.items():
            if not isinstance(tool, str) or not tool.strip():
                raise ValueError("tool names must be nonempty")
            if not isinstance(names, frozenset):
                raise TypeError("protected tool argument names must be frozenset[str]")
            if not all(isinstance(name, str) and name.strip() for name in names):
                raise ValueError("protected argument names must be nonempty")
            fields_snapshot[tool] = frozenset(names)
        self._sources = MappingProxyType(source_snapshot)
        self._protected_fields = MappingProxyType(fields_snapshot)

    def evaluate(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        provenance: ActionProvenance | None,
    ) -> BoundaryVerdict:
        """Deny unknown tools, missing lineage and data promoted to control."""
        protected = self._protected_fields.get(tool_name)
        if protected is None:
            return BoundaryVerdict(False, "tool_boundary_unregistered")
        if not protected:
            return BoundaryVerdict(True, "allowed")
        if provenance is None:
            return BoundaryVerdict(False, "missing_control_provenance")

        for field_name in sorted(protected):
            # An omitted high-impact parameter might take a dangerous default.
            if field_name not in arguments:
                return BoundaryVerdict(False, "protected_field_missing", field_name)
            controls = provenance.control_origins.get(field_name)
            if not controls:
                return BoundaryVerdict(False, "missing_control_provenance", field_name)
            data_sources = provenance.data_origins.get(field_name, frozenset())
            for source_id in sorted(controls | data_sources):
                label = self._sources.get(source_id)
                if label is None:
                    return BoundaryVerdict(False, "unknown_control_source", field_name)
                if label.trust != SourceTrust.AUTHORITY:
                    reason = (
                        "data_promoted_to_control"
                        if source_id in data_sources
                        else "untrusted_control_source"
                    )
                    return BoundaryVerdict(False, reason, field_name)
        return BoundaryVerdict(True, "allowed")
