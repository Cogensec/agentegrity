"""MCP tools/call execution boundary for host-owned tool registries.

This bridge handles the shape of a Model Context Protocol tool invocation; it
is not an MCP client, server or transparent proxy. Install it in the trusted
host's actual tool dispatch path, *before* executing the tool.

MCP tool descriptions and LLM-originated params are untrusted. Neither can
construct a TaskMandate, ToolAuthority, ActionProvenance or executor registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agentegrity.core.action_security import ActionExecution
from agentegrity.core.trust_boundaries import ActionProvenance
from agentegrity.sdk.gateway import TrustedActionGateway


class MCPToolCallError(ValueError):
    """Malformed tools/call parameters. Never carries attacker-controlled text."""


class MCPToolBoundary:
    """Apply a trusted ActionSecurityGate at the MCP tools/call boundary.

    The caller must bind this to an MCP server's actual tool dispatcher and
    construct the gateway from trusted server configuration. `arguments`
    must be a JSON object, not a model-supplied authority declaration.

    `action_id` and `provenance` must be generated or verified by trusted
    host instrumentation (not read from client params or protocol metadata).
    The RPC request ID is deliberately not used as the action ID: it is an
    untrusted correlation field and is not durable idempotency.
    """

    def __init__(self, gateway: TrustedActionGateway) -> None:
        if not isinstance(gateway, TrustedActionGateway):
            raise TypeError("gateway must be a TrustedActionGateway")
        self._gateway = gateway

    @property
    def gateway(self) -> TrustedActionGateway:
        """Host-owned gateway for effect reconciliation and revocation."""
        return self._gateway

    async def call_tool(
        self,
        params: Mapping[str, Any],
        *,
        action_id: str | None = None,
        now: datetime | None = None,
        provenance: ActionProvenance | None = None,
    ) -> ActionExecution[Any]:
        """Authorize then execute an MCP tools/call in the trusted server.

        The result is the registered host tool's *native* value. MCP servers
        must serialize it using their protocol SDK. The attached receipt
        remains PENDING (not proof of external completion or safe recovery).
        AuthorizationDenied is propagated: the host must convert it to its
        own protocol-specific tool error without leaking mandate contents.
        """
        if not isinstance(params, Mapping):
            raise MCPToolCallError("tools/call params must be an object")
        if set(params) - {"name", "arguments"}:
            raise MCPToolCallError("unexpected tools/call parameter")
        name = params.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise MCPToolCallError("tools/call name must be a valid string")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise MCPToolCallError("tools/call arguments must be an object")
        # No identity, approval, credential or source-assurance field from
        # params is ever trusted. Task authority comes from the gateway.
        return await self._gateway.ainvoke(
            name,
            arguments,
            action_id=action_id,
            now=now,
            provenance=provenance,
        )
