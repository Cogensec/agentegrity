"""Local small-model classifier for the adversarial layer.

:class:`AdversarialSLMLayer` is :class:`AdversarialLLMLayer` with the
transport swapped: instead of the Anthropic API it speaks the
OpenAI-compatible chat-completions protocol, so any local inference
server works — Ollama, llama.cpp, vLLM, LM Studio — on CPU or GPU.
The transport is stdlib :mod:`urllib` on a worker thread, so no
vendor SDK and no extra dependency is required. Usage::

    from agentegrity.layers.adversarial_slm import AdversarialSLMLayer

    layer = AdversarialSLMLayer(model="qwen2.5:3b")  # Ollama default URL

Everything else — the regex-taxonomy floor, verdict composition,
bounded concurrency, the (channel, content-hash) verdict cache, and
fail-open semantics — is inherited unchanged. Sync ``evaluate()``
never touches the network; only ``aevaluate()`` classifies.

Configuration resolves constructor args first, then environment::

    AGENTEGRITY_SLM_BASE_URL   default http://localhost:11434/v1
    AGENTEGRITY_SLM_MODEL      required (no sane universal default)
    AGENTEGRITY_SLM_API_KEY    optional bearer token
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request

from agentegrity.layers.adversarial_llm import (
    _SYSTEM_PROMPT,
    AdversarialLLMLayer,
    LLMAdversarialAssessment,
    parse_verdict,
)

logger = logging.getLogger("agentegrity.layers.adversarial_slm")

DEFAULT_BASE_URL = "http://localhost:11434/v1"


class AdversarialSLMLayer(AdversarialLLMLayer):
    """AdversarialLLMLayer backed by an OpenAI-compatible endpoint.

    Parameters
    ----------
    base_url : str, optional
        Root of the OpenAI-compatible API (the ``/chat/completions``
        suffix is appended). Falls back to ``AGENTEGRITY_SLM_BASE_URL``
        then the Ollama default ``http://localhost:11434/v1``.
    model : str, optional
        Model name as the server knows it. Falls back to
        ``AGENTEGRITY_SLM_MODEL``. When unresolved the layer fails
        open (with one warning) instead of guessing a model.
    api_key : str, optional
        Bearer token, for servers that require one. Falls back to
        ``AGENTEGRITY_SLM_API_KEY``; omitted from the request when
        unset (local servers usually need none).
    timeout : float
        Per-request timeout in seconds. Default 8.0.
    max_tokens : int
        Completion budget for the verdict JSON. Default 256.

    Remaining keyword arguments are forwarded to
    :class:`AdversarialLLMLayer` (coherence_threshold, patterns,
    extra_patterns, max_concurrency, ...).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 8.0,
        max_tokens: int = 256,
        **kwargs: object,
    ) -> None:
        super().__init__(timeout=timeout, **kwargs)  # type: ignore[arg-type]
        self._slm_base_url = (
            base_url
            or os.environ.get("AGENTEGRITY_SLM_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self._slm_model = model or os.environ.get("AGENTEGRITY_SLM_MODEL") or ""
        self._slm_api_key = api_key or os.environ.get("AGENTEGRITY_SLM_API_KEY")
        self._slm_timeout = timeout
        self._slm_max_tokens = max_tokens
        self._warned_no_model = False

    async def _classify_text(self, text: str) -> LLMAdversarialAssessment:
        """Classify via the OpenAI-compatible endpoint. Fails open."""
        if not self._slm_model:
            if not self._warned_no_model:
                self._warned_no_model = True
                logger.warning(
                    "No SLM model configured (model= or "
                    "AGENTEGRITY_SLM_MODEL); AdversarialSLMLayer fails open"
                )
            return LLMAdversarialAssessment.neutral()
        return await asyncio.to_thread(self._classify_sync, text)

    def _classify_sync(self, text: str) -> LLMAdversarialAssessment:
        payload = json.dumps(
            {
                "model": self._slm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
                "max_tokens": self._slm_max_tokens,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._slm_api_key:
            headers["Authorization"] = f"Bearer {self._slm_api_key}"
        request = urllib.request.Request(
            f"{self._slm_base_url}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._slm_timeout
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
            if not isinstance(raw, str):
                raise TypeError("non-string completion content")
        except Exception as exc:  # noqa: BLE001 — fail-open on any error
            logger.warning("SLM classify failed (%s); fail-open", exc)
            return LLMAdversarialAssessment.neutral()
        return parse_verdict(raw)


__all__ = ["DEFAULT_BASE_URL", "AdversarialSLMLayer"]
