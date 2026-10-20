"""Task-bound tool authorization and honest downstream-effect accounting.

An application installs a TaskMandate and tool registry from a trusted control
plane. Never construct these from model prompts, MCP descriptions or tool
responses. A hook verdict is advisory when its host cannot enforce denial;
use ``invoke`` at the actual tool execution boundary for a hard gate.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import uuid4

from agentegrity.core.trust_boundaries import ActionProvenance, TrustBoundaryPolicy

_T = TypeVar("_T")


class EffectType(str, Enum):
    READ = "read"
    WRITE = "write"
    EXTERNAL_SEND = "external_send"
    FINANCIAL_WRITE = "financial_write"
    CODE_EXECUTION = "code_execution"
    UNKNOWN = "unknown"


class EffectStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    FAILED = "failed"


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class ToolAuthority:
    """Trusted declaration of a tool's effect and resource extraction.

    Exactly one of ``resource_argument`` and ``fixed_resource`` is required.
    Classifying a tool as read-only does not make it read-only: the deployment
    must enforce that the tool cannot have undeclared effects.
    """

    effect: EffectType
    resource_argument: str | None = None
    fixed_resource: str | None = None
    credential_id: str | None = None

    def __post_init__(self) -> None:
        if bool(self.resource_argument) == bool(self.fixed_resource):
            raise ValueError("declare exactly one resource_argument or fixed_resource")
        if self.effect == EffectType.UNKNOWN:
            raise ValueError("unknown effects cannot be explicitly authorized")


@dataclass(frozen=True)
class TaskMandate:
    """Trusted, time-limited grant bound to an agent and optional run."""

    mandate_id: str
    principal_id: str
    agent_id: str
    expires_at: datetime
    allowed_tools: frozenset[str]
    allowed_resources: frozenset[str]
    allowed_effects: frozenset[EffectType]
    allowed_credentials: frozenset[str] = field(default_factory=frozenset)
    credential_expires_at: datetime | None = None
    run_id: str | None = None
    not_before: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.mandate_id, self.principal_id, self.agent_id)):
            raise ValueError("mandate, principal and agent identities are required")
        if not _aware(self.expires_at):
            raise ValueError("expires_at must be timezone-aware")
        if self.not_before is not None and not _aware(self.not_before):
            raise ValueError("not_before must be timezone-aware")
        if self.credential_expires_at is not None and not _aware(self.credential_expires_at):
            raise ValueError("credential_expires_at must be timezone-aware")
        for scoped in (self.allowed_tools, self.allowed_resources, self.allowed_credentials):
            if "*" in scoped:
                raise ValueError("wildcard grants are not supported")
        if EffectType.UNKNOWN in self.allowed_effects:
            raise ValueError("unknown effects cannot be authorized")


@dataclass(frozen=True)
class ActionIntent:
    action_id: str
    run_id: str
    agent_id: str
    tool_name: str
    resource: str
    effect: EffectType
    credential_id: str | None


@dataclass(frozen=True)
class AuthorizationVerdict:
    intent: ActionIntent
    allowed: bool
    reason: str
    mandate_id: str

    def to_dict(self) -> dict[str, Any]:
        """Safe to export: no argument values, credentials or raw source text."""
        return {
            "action_id": self.intent.action_id,
            "run_id": self.intent.run_id,
            "agent_id": self.intent.agent_id,
            "tool_name": self.intent.tool_name,
            "resource": self.intent.resource,
            "effect": self.intent.effect.value,
            "credential_id": self.intent.credential_id,
            "mandate_id": self.mandate_id,
            "allowed": self.allowed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EffectReceipt:
    action_id: str
    status: EffectStatus
    evidence_source: str = "sdk"
    evidence_reference: str | None = None
    independently_verified: bool = False

    @property
    def assurance(self) -> str:
        if self.status == EffectStatus.CONFIRMED and self.independently_verified:
            return "verified"
        return "unverified"


@dataclass(frozen=True)
class ActionExecution(Generic[_T]):
    verdict: AuthorizationVerdict
    result: _T
    receipt: EffectReceipt


class AuthorizationDenied(PermissionError):
    def __init__(self, verdict: AuthorizationVerdict):
        self.verdict = verdict
        super().__init__(f"agentegrity.denied:action_authority:{verdict.reason}")


class ActionSecurityGate:
    """Opt-in, deterministic policy gate with side-effect uncertainty tracking.

    ``check`` is appropriate for adapter pre-tool hooks. ``invoke`` is the
    execution-boundary variant: it passes the *same checked arguments* to the
    executor and never invokes it after denial. Neither method can verify that
    a declared tool has no hidden effects. Only a trusted external verifier
    can promote a completed effect receipt to independently verified.
    """

    def __init__(
        self,
        mandate: TaskMandate,
        tools: Mapping[str, ToolAuthority],
        *,
        trust_boundary: TrustBoundaryPolicy | None = None,
    ):
        self.mandate = mandate
        self._tools = dict(tools)
        # Host-controlled policy. Never derived from an agent or tool payload.
        self._trust_boundary = trust_boundary
        self._lock = threading.RLock()
        self._decisions: dict[str, AuthorizationVerdict] = {}
        self._receipts: dict[str, EffectReceipt] = {}

    def check(
        self,
        *,
        agent_id: str,
        run_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        action_id: str | None = None,
        now: datetime | None = None,
        provenance: ActionProvenance | None = None,
    ) -> AuthorizationVerdict:
        when = now if now is not None else datetime.now(timezone.utc)
        if not _aware(when):
            raise ValueError("now must be timezone-aware")
        identifier = action_id or uuid4().hex
        with self._lock:
            declared = self._tools.get(tool_name)
            resource = "<unresolved>"
            effect = EffectType.UNKNOWN
            credential = None
            if declared is not None:
                effect = declared.effect
                credential = declared.credential_id
                value = (arguments.get(declared.resource_argument)
                         if declared.resource_argument else declared.fixed_resource)
                if isinstance(value, str) and value.strip():
                    resource = value

            intent = ActionIntent(identifier, run_id, agent_id, tool_name,
                                  resource, effect, credential)
            reason = "allowed"
            if identifier in self._decisions:
                reason = "duplicate_action_id"
            elif agent_id != self.mandate.agent_id:
                reason = "agent_identity_mismatch"
            elif self.mandate.run_id is not None and run_id != self.mandate.run_id:
                reason = "run_identity_mismatch"
            elif self.mandate.not_before is not None and when < self.mandate.not_before:
                reason = "mandate_not_yet_valid"
            elif when >= self.mandate.expires_at:
                reason = "mandate_expired"
            elif declared is None or tool_name not in self.mandate.allowed_tools:
                reason = "tool_not_authorized"
            elif resource == "<unresolved>" or resource not in self.mandate.allowed_resources:
                reason = "resource_not_authorized"
            elif effect not in self.mandate.allowed_effects:
                reason = "effect_not_authorized"
            elif credential is not None and credential not in self.mandate.allowed_credentials:
                reason = "credential_not_authorized"
            elif (credential is not None and self.mandate.credential_expires_at is not None
                  and when >= self.mandate.credential_expires_at):
                reason = "credential_expired"
            elif self._trust_boundary is not None:
                boundary = self._trust_boundary.evaluate(tool_name, arguments, provenance)
                if not boundary.allowed:
                    reason = boundary.reason

            verdict = AuthorizationVerdict(intent, reason == "allowed", reason,
                                           self.mandate.mandate_id)
            if identifier not in self._decisions:
                self._decisions[identifier] = verdict
            # Authorization alone does not mean the tool has started.
            # Only the execution-boundary wrappers create a pending receipt.
            return verdict

    def _begin_execution(self, verdict: AuthorizationVerdict) -> EffectReceipt:
        """Register an attempted execution only after all authorization gates pass."""
        with self._lock:
            identifier = verdict.intent.action_id
            if not verdict.allowed or self._decisions.get(identifier) is not verdict:
                raise ValueError("execution requires an authorized decision")
            if identifier in self._receipts:
                raise ValueError("action execution already started")
            receipt = EffectReceipt(identifier, EffectStatus.PENDING)
            self._receipts[identifier] = receipt
            return receipt

    def receipt_for(self, action_id: str) -> EffectReceipt | None:
        with self._lock:
            return self._receipts.get(action_id)

    def record_effect(
        self,
        receipt: EffectReceipt,
        *,
        verifier: Callable[[EffectReceipt], bool] | None = None,
    ) -> EffectReceipt:
        """Reconcile a result; never trust a client-side 'verified' flag.

        The host chooses the verifier; it MUST check independent evidence
        from the acting system and never derive approval from agent messages.
        """
        with self._lock:
            verdict = self._decisions.get(receipt.action_id)
            if verdict is None or not verdict.allowed:
                raise ValueError("effect receipt has no authorized action")
            previous = self._receipts.get(receipt.action_id)
            if previous is None:
                raise ValueError("missing pending effect receipt")
            if previous.independently_verified:
                raise ValueError("verified effect receipt cannot be overwritten")
            verified = False
            if (verifier is not None and receipt.evidence_reference
                    and receipt.evidence_source != "sdk"):
                try:
                    verified = verifier(receipt) is True
                except Exception:
                    verified = False
            stored = replace(receipt, independently_verified=verified)
            self._receipts[receipt.action_id] = stored
            return stored

    def invoke(
        self,
        *,
        agent_id: str,
        run_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        executor: Callable[..., _T],
        action_id: str | None = None,
        now: datetime | None = None,
        provenance: ActionProvenance | None = None,
    ) -> ActionExecution[_T]:
        """Enforce before calling a tool; success does NOT verify effects."""
        checked_arguments = dict(arguments)
        verdict = self.check(
            agent_id=agent_id, run_id=run_id, tool_name=tool_name,
            arguments=checked_arguments, action_id=action_id, now=now,
            provenance=provenance,
        )
        if not verdict.allowed:
            raise AuthorizationDenied(verdict)
        if inspect.iscoroutinefunction(executor):
            # An async function does not execute until awaited. Do not create a receipt.
            raise TypeError("async executor requires ActionSecurityGate.ainvoke()")
        receipt = self._begin_execution(verdict)
        try:
            result = executor(**checked_arguments)
            if inspect.isawaitable(result):
                # A sync-looking callable may also return a coroutine or Future.
                # Do not report a result until execution actually completed.
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("awaitable executor result requires ActionSecurityGate.ainvoke()")
        except BaseException:
            # A callable may have committed an external write before raising.
            self.record_effect(EffectReceipt(verdict.intent.action_id, EffectStatus.UNKNOWN))
            raise
        return ActionExecution(verdict, result, receipt)

    async def ainvoke(
        self,
        *,
        agent_id: str,
        run_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        executor: Callable[..., Awaitable[_T]],
        action_id: str | None = None,
        now: datetime | None = None,
        provenance: ActionProvenance | None = None,
    ) -> ActionExecution[_T]:
        """Await an authorized async tool; exceptions and cancellation leave effects unknown."""
        checked_arguments = dict(arguments)
        verdict = self.check(
            agent_id=agent_id, run_id=run_id, tool_name=tool_name,
            arguments=checked_arguments, action_id=action_id, now=now,
            provenance=provenance,
        )
        if not verdict.allowed:
            raise AuthorizationDenied(verdict)
        receipt = self._begin_execution(verdict)
        try:
            result = await executor(**checked_arguments)
        except BaseException:
            self.record_effect(EffectReceipt(verdict.intent.action_id, EffectStatus.UNKNOWN))
            raise
        return ActionExecution(verdict, result, receipt)
