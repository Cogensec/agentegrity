"""
LangChain / LangGraph adapter for agentegrity.

Covers **both** LangChain agents and LangGraph compiled graphs. LangGraph
propagates events through LangChain's callback surface
(``langchain_core.callbacks.BaseCallbackHandler``), so a single callback
handler catches tool + chain + llm events from either framework.

Event mapping:
    on_chain_start (top-level)   -> user_prompt_submit
    on_tool_start / on_tool_end  -> pre_tool_use / post_tool_use
    on_tool_error                -> post_tool_use_failure
    on_chain_start (sub-chain)   -> subagent_start  (graph nodes)
    on_chain_end (top-level)     -> stop

Usage (LangChain):
    from agentegrity.langchain import instrument_chain, report
    chain = instrument_chain(my_chain)
    chain.invoke({"input": "..."})
    print(report())

Usage (LangGraph):
    from agentegrity.langchain import instrument_graph, report
    graph = instrument_graph(my_compiled_graph)
    graph.invoke(state)
    print(report())
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from agentegrity.adapters.base import _BaseAdapter

logger = logging.getLogger("agentegrity.adapters.langchain")


class LangChainAdapter(_BaseAdapter):
    """Instruments a LangChain chain or LangGraph graph with agentegrity."""

    _name = "langchain"

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        """Dispatch an event synchronously from the callback thread.

        LangChain's ``BaseCallbackHandler`` supports both sync and async
        callbacks. We use the sync entry points and run the async
        ``on_event`` handler on the current (or a new) event loop.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.on_event(event_type, data))
                return
        except RuntimeError:
            pass
        try:
            asyncio.run(self.on_event(event_type, data))
        except Exception as exc:
            logger.warning("langchain dispatch %s failed: %s", event_type, exc)

    def create_callback_handler(self) -> Any:
        """Return a ``BaseCallbackHandler`` subclass instance bound to this adapter.

        Import ``BaseCallbackHandler`` at call time so the adapter module
        can be imported without ``langchain-core`` installed.
        """
        try:
            from langchain_core.callbacks import (
                BaseCallbackHandler,
            )
        except ImportError:
            raise ImportError(
                "langchain-core is required for the LangChain adapter. "
                "Install it with: pip install agentegrity[langchain]"
            ) from None

        adapter = self

        class _AgentegrityCallbackHandler(BaseCallbackHandler):  # type: ignore[misc, unused-ignore]
            """LangChain callback handler forwarding to the adapter."""

            def on_chain_start(
                self,
                serialized: dict[str, Any] | None,
                inputs: dict[str, Any],
                *,
                run_id: UUID,
                parent_run_id: UUID | None = None,
                **kwargs: Any,
            ) -> None:
                if parent_run_id is None:
                    prompt = ""
                    if isinstance(inputs, dict):
                        prompt = str(
                            inputs.get("input")
                            or inputs.get("question")
                            or inputs.get("messages")
                            or inputs
                        )
                    adapter._dispatch("user_prompt_submit", {"prompt": prompt})
                else:
                    name = (serialized or {}).get("name", "chain") if serialized else "chain"
                    adapter._dispatch(
                        "subagent_start",
                        {"agent_id": str(run_id), "name": name},
                    )

            def on_chain_end(
                self,
                outputs: dict[str, Any],
                *,
                run_id: UUID,
                parent_run_id: UUID | None = None,
                **kwargs: Any,
            ) -> None:
                if parent_run_id is None:
                    adapter._dispatch("stop", {"outputs": outputs})

            def on_tool_start(
                self,
                serialized: dict[str, Any] | None,
                input_str: str,
                *,
                run_id: UUID,
                parent_run_id: UUID | None = None,
                **kwargs: Any,
            ) -> None:
                tool_name = (serialized or {}).get("name", "tool") if serialized else "tool"
                adapter._dispatch(
                    "pre_tool_use",
                    {"tool_name": tool_name, "tool_input": {"input": input_str}},
                )

            def on_tool_end(
                self,
                output: Any,
                *,
                run_id: UUID,
                parent_run_id: UUID | None = None,
                **kwargs: Any,
            ) -> None:
                adapter._dispatch(
                    "post_tool_use",
                    {"tool_name": kwargs.get("name", ""), "tool_response": str(output)},
                )

            def on_tool_error(
                self,
                error: BaseException,
                *,
                run_id: UUID,
                parent_run_id: UUID | None = None,
                **kwargs: Any,
            ) -> None:
                adapter._dispatch(
                    "post_tool_use_failure",
                    {"tool_name": kwargs.get("name", ""), "error": str(error)},
                )

        return _AgentegrityCallbackHandler()

    def instrument_chain(self, chain: Any) -> Any:
        """Attach the agentegrity callback handler to a LangChain runnable.

        Uses ``Runnable.with_config(callbacks=[handler])`` which is the
        standard LangChain Expression Language (LCEL) instrumentation
        point. Returns the wrapped runnable — the original is unchanged.
        """
        handler = self.create_callback_handler()
        if hasattr(chain, "with_config"):
            return chain.with_config({"callbacks": [handler]})
        # Legacy chains: set .callbacks attribute directly
        existing = list(getattr(chain, "callbacks", None) or [])
        existing.append(handler)
        try:
            chain.callbacks = existing
        except Exception:
            pass
        return chain

    def instrument_graph(self, graph: Any) -> Any:
        """Attach the agentegrity callback handler to a LangGraph compiled graph.

        LangGraph graphs use the same ``Runnable.with_config`` interface,
        so this is functionally identical to ``instrument_chain`` but
        documented separately for discoverability.
        """
        return self.instrument_chain(graph)
