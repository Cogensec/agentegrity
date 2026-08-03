"""First-class human-in-the-loop approval for escalate verdicts.

The enforcement seam has always accepted an ``approval_handler``
callable returning a bool, failing closed on exceptions. This module
promotes that seam into a documented workflow:

* :class:`ApprovalDecision` — a rich resolution carrying who decided,
  when, why, and whether the request timed out. Handlers may return
  one directly instead of a bool.
* :class:`ApprovalWorkflow` — wraps any handler with a timeout policy.
  The default on timeout is **deny** (fail-closed); passing
  ``timeout_action="allow"`` makes fail-open an explicit, visible
  choice rather than a silent default.

Under enforcement, adapters record every consulted approval as a
``DecisionRecord`` on the attestation chain (signed when the adapter
has a signing key), so "who approved this and when" is part of the
verifiable session provenance.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

logger = logging.getLogger("agentegrity.approval")


@dataclass
class ApprovalDecision:
    """Resolution of one escalate-approval request."""

    approved: bool
    approver: str = "approval_handler"
    reason: str | None = None
    timed_out: bool = False
    requested_at: float = field(default_factory=time.time)
    resolved_at: float = field(default_factory=time.time)

    def provenance_steps(self) -> list[str]:
        """Reasoning-chain lines recorded on the attestation chain."""
        steps = [
            f"outcome:{'approved' if self.approved else 'denied'}",
            f"approver:{self.approver}",
            f"timed_out:{'true' if self.timed_out else 'false'}",
            f"requested_at:{self.requested_at:.3f}",
            f"resolved_at:{self.resolved_at:.3f}",
        ]
        if self.reason:
            steps.append(f"reason:{self.reason}")
        return steps


class ApprovalWorkflow:
    """Timeout-bounded approval handler producing rich decisions.

    Parameters
    ----------
    handler : callable
        Receives ``(profile, score, candidate_action)`` — the same
        signature the raw ``approval_handler`` seam uses — and returns
        a bool or an :class:`ApprovalDecision`.
    approver : str
        Identity recorded for bool-returning handlers. A handler that
        returns its own :class:`ApprovalDecision` overrides this.
    timeout : float, optional
        Seconds to wait for the handler. ``None`` waits indefinitely.
        The handler runs on a worker thread; on timeout the thread is
        abandoned (its eventual result is discarded) and the timeout
        policy decides.
    timeout_action : "deny" | "allow"
        What a timeout resolves to. Default ``"deny"`` — an unanswered
        approval request fails closed.
    """

    def __init__(
        self,
        handler: Callable[..., Any],
        *,
        approver: str = "approval_handler",
        timeout: float | None = None,
        timeout_action: Literal["deny", "allow"] = "deny",
    ) -> None:
        if timeout_action not in ("deny", "allow"):
            raise ValueError(
                f"timeout_action must be 'deny' or 'allow', got {timeout_action!r}"
            )
        self._handler = handler
        self._approver = approver
        self._timeout = timeout
        self._timeout_action = timeout_action

    def __call__(
        self, profile: Any, score: Any, candidate_action: dict[str, Any]
    ) -> ApprovalDecision:
        requested_at = time.time()
        try:
            if self._timeout is None:
                raw = self._handler(profile, score, candidate_action)
            else:
                # No `with` block: __exit__ would join the worker thread,
                # blocking for as long as the stuck handler runs and
                # defeating the timeout. shutdown(wait=False) abandons it.
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(
                    self._handler, profile, score, candidate_action
                )
                try:
                    raw = future.result(timeout=self._timeout)
                except concurrent.futures.TimeoutError:
                    approved = self._timeout_action == "allow"
                    logger.warning(
                        "approval request timed out after %.1fs; %s "
                        "(timeout_action=%s)",
                        self._timeout,
                        "allowing" if approved else "denying fail-closed",
                        self._timeout_action,
                    )
                    return ApprovalDecision(
                        approved=approved,
                        approver=self._approver,
                        reason=f"timed out after {self._timeout}s",
                        timed_out=True,
                        requested_at=requested_at,
                        resolved_at=time.time(),
                    )
                finally:
                    pool.shutdown(wait=False)
        except Exception as exc:  # noqa: BLE001 — approval must fail closed
            logger.warning("approval handler raised (%s); denying", exc)
            return ApprovalDecision(
                approved=False,
                approver=self._approver,
                reason=f"handler raised {type(exc).__name__}",
                requested_at=requested_at,
                resolved_at=time.time(),
            )

        if isinstance(raw, ApprovalDecision):
            raw.requested_at = requested_at
            return raw
        return ApprovalDecision(
            approved=bool(raw),
            approver=self._approver,
            requested_at=requested_at,
            resolved_at=time.time(),
        )


__all__ = ["ApprovalDecision", "ApprovalWorkflow"]
