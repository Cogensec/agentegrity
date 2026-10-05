"""One instrumented hook session: adapter state, host verdicts, chain persistence.

The session holds the adapter for the whole conversation, so the layers
see every prompt, tool call, output and failure in order: output
scanning, cross-call egress (GOV-005) and compaction recovery all work.
The daemon in :mod:`agentegrity.hooks.daemon` keeps one session alive
per host conversation.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentegrity.adapters.hook_hosts import ADAPTERS_BY_HOST
from agentegrity.core.attestation import AttestationChain
from agentegrity.core.evaluator import IntegrityEvaluator, IntegrityScore
from agentegrity.core.profile import AgentProfile, RiskTier
from agentegrity.core.tool_classifier import PATCH_MARKER
from agentegrity.hooks.protocol import normalize, render_decision, verdict_for
from agentegrity.layers import default_layers
from agentegrity.layers.adversarial import AdversarialLayer, default_detector_patterns
from agentegrity.layers.baseline_store import FileBaselineStore
from agentegrity.layers.checkpoint import validate_storage_identifier

# Tool arguments are structured by construction, so the action_injection
# patterns that key on quotes and braces would fire on ordinary code.
_STRUCTURE_CUE_PATTERNS = frozenset(
    {
        "embedded_polite_directive",
        "embedded_field_imperative",
        "spliced_capitalized_imperative",
    }
)
# Threats from the current call (its arguments or the content it writes)
# this strong escalate even when nothing blocks. Session-wide evidence,
# such as an injected page still in context, never escalates on its own,
# or one poisoned read would deny every later call on Codex.
_ESCALATE_SEVERITY = 0.70
_ESCALATE_CONFIDENCE = 0.60
_ACTION_RANK = {"pass": 0, "alert": 1, "escalate": 2, "block": 3}
_WRITTEN_FIELDS = ("content", "new_string", "new_source")


class HookSession:
    """Evaluate one host conversation's hook events and decide each tool call."""

    def __init__(self,
        host: str,
        session_id: str,
        chain_path: Path,
        *,
        mode: str = "enforce",
        risk_tier: RiskTier = RiskTier.HIGH,
        stream: bool = True,
        agent_id: str | None = None,
        agent_name: str | None = None,
        model_id: str | None = None,
    ) -> None:
        """Build the session, resuming the chain persisted at ``chain_path``."""
        self.host = host
        self.session_id = session_id
        self.chain_path = chain_path
        self.mode = mode
        self.ended = False
        # The console groups sessions by agent_id and labels them by name;
        # both default to the host so an unconfigured install still groups.
        profile = AgentProfile.default(name=agent_name or host)
        profile.agent_id = agent_id or host
        # Hosts let the user switch models mid-session, so this is the
        # configured model, not proof of the one that served each call.
        profile.model_id = model_id or None
        profile.risk_tier = risk_tier
        profile.metadata = {"host": host, "session_id": session_id}
        store = _baseline_store(chain_path.parent, profile.agent_id)
        evaluator = IntegrityEvaluator(layers=default_layers(baseline_store=store))
        self._adapter = ADAPTERS_BY_HOST[host](
            profile=profile, evaluator=evaluator, chain=_load_chain(chain_path),
            stream_from_env=stream,
        )
        self._written = AdversarialLayer(
            patterns=[
                p for p in default_detector_patterns() if p.name not in _STRUCTURE_CUE_PATTERNS
            ],
            detect_tool_sequences=False,
            detect_tool_arguments=False,
        )
        self._persisted_records = len(self._adapter.attestation_chain.records)

    def handle(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Process one hook payload; return the host output, or None to stay silent."""
        event = normalize(self.host, payload)
        if event is None or self.ended:
            return None
        if event.kind == "session_end":
            self.close()
            return None
        self._adapter._evaluate_sync(event.kind, event.data)
        if event.kind != "pre_tool_use":
            self.persist()
            return None
        tool = event.data["tool_name"]
        arguments = event.data["tool_input"]
        action, reasons = self._assess(self._latest_score(), tool, arguments)
        verdict = verdict_for(self.host, action)
        if verdict != "allow":
            self._adapter.record_decision(
                decision_point="hook_verdict",
                candidate_action={"tool": tool, "arguments": arguments},
                reasoning_chain=[f"verdict:{verdict}", f"mode:{self.mode}", *reasons],
            )
        self.persist()
        if self.mode == "alert":
            return None
        return render_decision(verdict, "agentegrity: " + "; ".join(reasons))

    def close(self) -> None:
        """End the session: close the adapter, flush exporters, persist the chain."""
        if self.ended:
            return
        self.ended = True
        self._adapter.close()
        for exporter in self._adapter._exporters:
            flush = getattr(exporter, "flush", None)
            if callable(flush):
                flush()
        self.persist()

    def persist(self) -> None:
        """Write the chain atomically (mode 0600) when it has new records."""
        chain = self._adapter.attestation_chain
        if len(chain.records) == self._persisted_records:
            return
        self.chain_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp_path = self.chain_path.with_name(self.chain_path.name + ".tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(chain.to_json())
        os.replace(tmp_path, self.chain_path)
        self._persisted_records = len(chain.records)

    def _latest_score(self) -> IntegrityScore:
        """The evaluation the adapter just ran for this tool call."""
        return next(
            e.evaluation_result
            for e in reversed(self._adapter.events)
            if e.evaluation_result is not None
        )

    def _assess(self,
        score: IntegrityScore,
        tool: str,
        arguments: Mapping[str, Any],
    ) -> tuple[str, list[str]]:
        """Combine the adapter's score with a scan of content the call writes."""
        action = score.action
        reasons: list[str] = []
        for layer in score.layer_results:
            for threat in layer.details.get("threats", []):
                reasons.append(f"{threat['threat_type']} ({threat['severity']:.2f})")
                if threat.get("channel") == "tool_arguments" and _strong(threat):
                    action = _max_action(action, "escalate")
            for evaluation in layer.details.get("evaluations", []):
                if evaluation.get("triggered"):
                    reasons.append(f"{evaluation['rule_id']} ({evaluation['decision']})")
        written = _written_text(arguments)
        if written:
            result = self._written.evaluate(self._adapter.profile, {"input": written})
            threats = result.details.get("threats", [])
            reasons.extend(f"written content: {t['threat_type']}" for t in threats)
            if result.action == "block":
                action = _max_action(action, "block")
            elif any(_strong(t) for t in threats):
                action = _max_action(action, "escalate")
        return action, list(dict.fromkeys(reasons))


def _strong(threat: Mapping[str, Any]) -> bool:
    """True for a threat strong enough to escalate on its own."""
    return bool(
        threat["severity"] >= _ESCALATE_SEVERITY and threat["confidence"] >= _ESCALATE_CONFIDENCE
    )


def _max_action(current: str, candidate: str) -> str:
    """The more severe of two score actions."""
    return candidate if _ACTION_RANK[candidate] > _ACTION_RANK.get(current, 0) else current


def _written_text(arguments: Mapping[str, Any]) -> str:
    """Text a call writes into files: Write/Edit fields and added patch lines."""
    parts = [arguments[f] for f in _WRITTEN_FIELDS if isinstance(arguments.get(f), str)]
    for edit in arguments.get("edits") or []:
        if isinstance(edit, Mapping) and isinstance(edit.get("new_string"), str):
            parts.append(edit["new_string"])
    command = arguments.get("command")
    if isinstance(command, list):
        command = "\n".join(str(c) for c in command)
    if isinstance(command, str) and PATCH_MARKER in command:
        parts.extend(
            line[1:] for line in command.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
    return "\n".join(parts)


def _baseline_store(state_dir: Path, agent_id: str) -> FileBaselineStore | None:
    """Persist drift baselines per agent under ``<state dir>/baselines``.

    An agent id that is unsafe as a filename gets an in-memory baseline
    instead: drift then only spans this session, but the hook keeps working.
    """
    try:
        validate_storage_identifier(agent_id, kind="agent_id")
    except ValueError:
        return None
    return FileBaselineStore(state_dir / "baselines")


def _load_chain(path: Path) -> AttestationChain | None:
    """Load a persisted chain; a corrupt file is moved aside, never overwritten."""
    if not path.exists():
        return None
    try:
        return AttestationChain.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        os.replace(path, path.with_name(path.name + ".corrupt"))
        return None
