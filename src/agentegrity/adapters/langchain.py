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

import logging
from typing import Any
from uuid import UUID

from agentegrity.adapters.base import _BaseAdapter

logger = logging.getLogger("agentegrity.adapters.langchain")


class LangChainAdapter(_BaseAdapter):
    """Instruments a LangChain chain or LangGraph graph with agentegrity."""

    _name = "langchain"

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
        so the callback handler wiring is identical to
        ``instrument_chain``.

        v0.8: also introspects the compiled graph via ``graph.get_graph()``
        to declare an :class:`AgentTopology` so the layers see the
        multi-agent structure. Heuristic for kind: if a node named
        ``"supervisor"`` exists, kind = HIERARCHICAL_DAG with supervisor +
        workers; otherwise kind = PEER_TO_PEER with all nodes as peers
        (covers the swarm pattern). If graph introspection fails (graph
        is uncompiled, lacks ``get_graph``, or raises), falls back to
        single-agent — no topology declaration.
        """
        result = self.instrument_chain(graph)
        try:
            self._maybe_declare_graph_topology(graph)
        except Exception as exc:
            logger.warning(
                "langchain instrument_graph could not introspect topology: %s",
                exc,
            )
        return result

    def _maybe_declare_graph_topology(self, graph: Any) -> None:
        """Introspect a LangGraph compiled graph and declare topology.

        LangGraph compiled graphs expose ``get_graph()`` returning a
        ``Graph`` with ``nodes`` (dict). We walk node keys, skip the
        builtin ``__start__`` / ``__end__`` sentinels, and produce
        either a HIERARCHICAL_DAG (supervisor + workers) or
        PEER_TO_PEER (swarm) topology.
        """
        get_graph = getattr(graph, "get_graph", None)
        if not callable(get_graph):
            return
        graph_obj = get_graph()
        nodes = getattr(graph_obj, "nodes", None)
        if not nodes:
            return

        node_keys = [
            k for k in nodes
            if k not in ("__start__", "__end__") and not k.startswith("__")
        ]
        if not node_keys:
            return

        from agentegrity.core.topology import (
            AgentMember,
            AgentRole,
            AgentTopology,
            TopologyKind,
        )

        supervisor_key = None
        for k in node_keys:
            if k.lower() in ("supervisor", "supervisor_agent", "orchestrator"):
                supervisor_key = k
                break

        if supervisor_key is not None:
            kind = TopologyKind.HIERARCHICAL_DAG
            members = [AgentMember(
                agent_id=supervisor_key,
                name=supervisor_key,
                role=AgentRole.SUPERVISOR,
                capabilities=("tool_use",),
            )]
            for k in node_keys:
                if k == supervisor_key:
                    continue
                members.append(AgentMember(
                    agent_id=k,
                    name=k,
                    role=AgentRole.WORKER,
                    parent_id=supervisor_key,
                    capabilities=("tool_use",),
                ))
            my_role = AgentRole.SUPERVISOR
        else:
            kind = TopologyKind.PEER_TO_PEER
            members = [AgentMember(
                agent_id=k,
                name=k,
                role=AgentRole.PEER,
                capabilities=("tool_use",),
            ) for k in node_keys]
            my_role = AgentRole.PEER

        topology = AgentTopology(
            kind=kind,
            members=tuple(members),
            comm_channels=frozenset({"peer_messages"}),
        )
        self.set_topology(topology, my_role=my_role)
