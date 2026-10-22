"""Opt-in, framework-independent gateway for trusted tool executors.

The *application host* constructs this gateway, mandate and registry. Agent
messages, MCP tool descriptions and retrieved content must not create grants.
Execution is protected only when every consequential call actually uses this
gateway (and is independently authorized by the downstream service).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agentegrity.core.action_security import (
    ActionExecution,
    ActionSecurityGate,
    AuthorizationDenied,
    TaskMandate,
    ToolAuthority,
)
from agentegrity.core.trust_boundaries import ActionProvenance, TrustBoundaryPolicy

MAX_GATEWAY_ARGUMENT_BYTES = 65536


@dataclass(frozen=True)
class GatewayTool:
    """A host-installed executor and its declared authority surface."""

    authority: ToolAuthority
    executor: Callable[..., Any]

    def __post_init__(self) -> None:
        if not isinstance(self.authority, ToolAuthority):
            raise TypeError("authority must be a ToolAuthority")
        if not callable(self.executor):
            raise TypeError("executor must be callable")


def _snapshot_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Isolate caller-owned mutable arguments and reject non-JSON objects.

    The snapshot is passed to *both* the authority check and the actual tool.
    No argument values are logged or added to exported verdicts.
    """
    if not isinstance(arguments, Mapping):
        raise TypeError("arguments must be a mapping")
    if not all(isinstance(key, str) and key for key in arguments):
        raise ValueError("argument keys must be nonempty strings")
    try:
        wire = json.dumps(dict(arguments), allow_nan=False, ensure_ascii=True)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("tool arguments must be finite JSON values") from exc
    if len(wire.encode("utf-8")) > MAX_GATEWAY_ARGUMENT_BYTES:
        raise ValueError("tool arguments exceed gateway size limit")
    decoded = json.loads(wire)
    if not isinstance(decoded, dict):
        raise ValueError("arguments must encode to a JSON object")
    return decoded


def _async_executor(executor: Callable[..., Any]) -> bool:
    """Include callable objects with async __call__, without invoking them."""
    return (
        inspect.iscoroutinefunction(executor)
        or inspect.iscoroutinefunction(getattr(executor, "__call__", None))
    )


class TrustedActionGateway:
    """Route every tool call through existing task-bound authority enforcement.

    The gateway binds agent/run identity from a host-created mandate; the
    agent cannot replace those values. The registry is copied on setup.
    Its `gate` is exposed only for trusted host reconciliation and revocation.

    This is process-local, not a remote proxy, distributed lock, sandbox,
    identity provider or independent downstream authorization service.
    """

    def __init__(
        self,
        mandate: TaskMandate,
        tools: Mapping[str, GatewayTool],
        *,
        trust_boundary: TrustBoundaryPolicy | None = None,
        evidence_timeout: timedelta | None = None,
    ) -> None:
        if not isinstance(mandate, TaskMandate) or not mandate.run_id:
            raise ValueError("gateway requires a trusted run-bound TaskMandate")
        registry: dict[str, GatewayTool] = {}
        for name, binding in tools.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("tool names must be nonempty")
            if not isinstance(binding, GatewayTool):
                raise TypeError("gateway registry entries must be GatewayTool")
            registry[name] = binding
        self._bindings = registry
        self._gate = ActionSecurityGate(
            mandate,
            {name: binding.authority for name, binding in registry.items()},
            trust_boundary=trust_boundary,
            evidence_timeout=evidence_timeout,
        )

    @property
    def gate(self) -> ActionSecurityGate:
        """Host-only interface for receipt verification, expiry and revocation."""
        return self._gate

    def _binding_or_deny(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        action_id: str | None,
        now: datetime | None,
        provenance: ActionProvenance | None,
    ) -> GatewayTool:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be nonempty")
        binding = self._bindings.get(tool_name)
        if binding is None:
            verdict = self._gate.check(
                agent_id=self._gate.mandate.agent_id,
                run_id=self._gate.mandate.run_id or "",
                tool_name=tool_name,
                arguments=arguments,
                action_id=action_id,
                now=now,
                provenance=provenance,
            )
            raise AuthorizationDenied(verdict)
        return binding

    def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        action_id: str | None = None,
        now: datetime | None = None,
        provenance: ActionProvenance | None = None,
    ) -> ActionExecution[Any]:
        """Run a synchronous host tool; forbidden actions never call it."""
        copied = _snapshot_arguments(arguments)
        binding = self._binding_or_deny(tool_name, copied, action_id, now, provenance)
        if _async_executor(binding.executor):
            raise TypeError("async executor requires ainvoke")
        return self._gate.invoke(
            agent_id=self._gate.mandate.agent_id,
            run_id=self._gate.mandate.run_id or "",
            tool_name=tool_name,
            arguments=copied,
            executor=binding.executor,
            action_id=action_id,
            now=now,
            provenance=provenance,
        )

    async def ainvoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        action_id: str | None = None,
        now: datetime | None = None,
        provenance: ActionProvenance | None = None,
    ) -> ActionExecution[Any]:
        """Await an asynchronous host tool through the same action gate."""
        copied = _snapshot_arguments(arguments)
        binding = self._binding_or_deny(tool_name, copied, action_id, now, provenance)
        if not _async_executor(binding.executor):
            raise TypeError("sync executor requires invoke")
        return await self._gate.ainvoke(
            agent_id=self._gate.mandate.agent_id,
            run_id=self._gate.mandate.run_id or "",
            tool_name=tool_name,
            arguments=copied,
            executor=binding.executor,
            action_id=action_id,
            now=now,
            provenance=provenance,
        )
