"""Token usage from each framework adapter, against the framework's own types.

Every adapter normalizes to the same shape: ``input_tokens`` includes
cached tokens, cache and reasoning counts are parts of input and output.
"""

from __future__ import annotations

import asyncio
from importlib.util import find_spec
from types import SimpleNamespace
from typing import Any

import pytest

from agentegrity.adapters.base import _BaseAdapter
from agentegrity.core.profile import AgentProfile


def _profile() -> AgentProfile:
    profile = AgentProfile.default(name="t")
    profile.model_id = "profile-model"
    return profile


def _usage(adapter: _BaseAdapter) -> dict[str, Any]:
    usage = adapter.get_summary().get("usage")
    assert usage is not None
    return dict(usage)


def _needs(package: str) -> pytest.MarkDecorator:
    # find_spec imports a dotted name's parent, which raises when it is missing.
    try:
        installed = find_spec(package) is not None
    except ModuleNotFoundError:
        installed = False
    return pytest.mark.skipif(not installed, reason=f"{package} not installed")


@_needs("agents")
class TestOpenAIAgents:
    def _response(self, response_id: str, inputs: int, cached: int, output: int,
                  reasoning: int) -> Any:
        from agents.items import ModelResponse

        return ModelResponse(output=[], usage=self._sdk_usage(inputs, cached, output, reasoning),
                             response_id=response_id)

    @staticmethod
    def _sdk_usage(inputs: int, cached: int, output: int, reasoning: int,
                   requests: int = 1) -> Any:
        from agents.usage import InputTokensDetails, OutputTokensDetails, Usage

        return Usage(requests=requests, input_tokens=inputs,
                     input_tokens_details=InputTokensDetails(cached_tokens=cached,
                                                                    cache_write_tokens=0),
                     output_tokens=output,
                     output_tokens_details=OutputTokensDetails(reasoning_tokens=reasoning),
                     total_tokens=inputs + output)

    def _run(self, adapter: Any, calls: list[tuple[Any, Any]], shared: Any) -> None:
        from agents.run_context import RunContextWrapper

        hooks = adapter.create_run_hooks()
        context = RunContextWrapper(context=None, usage=shared)
        for agent, response in calls:
            shared.add(response.usage)
            asyncio.run(hooks.on_llm_end(context, agent, response))

    def test_each_call_is_counted_with_its_agent_model(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile(), stream_from_env=False)
        agent = SimpleNamespace(name="a", model="gpt-5.5")
        shared = self._sdk_usage(0, 0, 0, 0, requests=0)
        self._run(adapter, [(agent, self._response("r1", 100, 60, 10, 4)),
                            (agent, self._response("r2", 200, 0, 20, 0))], shared)
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["cache_read_tokens"]) == (300, 60)
        assert (usage["output_tokens"], usage["reasoning_tokens"]) == (30, 4)
        assert usage["requests"] == 2
        assert list(usage["by_model"]) == ["gpt-5.5"]
        assert usage["sources"] == ["provider_response"]

    def test_agent_as_tool_calls_are_counted_from_the_shared_total(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile(), stream_from_env=False)
        outer = SimpleNamespace(name="outer", model="gpt-5.5")
        shared = self._sdk_usage(0, 0, 0, 0, requests=0)
        first, last = self._response("r1", 100, 0, 1, 0), self._response("r3", 200, 0, 1, 0)
        nested = self._sdk_usage(1000, 0, 5, 0)
        self._run(adapter, [(outer, first)], shared)
        shared.add(nested)  # an agent run as a tool shares the parent's Usage
        self._run(adapter, [(outer, last)], shared)
        usage = _usage(adapter)
        assert usage["input_tokens"] == 1300
        assert usage["requests"] == 3
        assert usage["by_model"]["unknown"]["input_tokens"] == 1000

    def test_model_object_and_missing_model(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile(), stream_from_env=False)
        shared = self._sdk_usage(0, 0, 0, 0, requests=0)
        self._run(adapter, [
            (SimpleNamespace(name="a", model=SimpleNamespace(model="gpt-obj")),
             self._response("r1", 1, 0, 1, 0)),
            (SimpleNamespace(name="b", model=None), self._response("r2", 1, 0, 1, 0)),
        ], shared)
        assert set(_usage(adapter)["by_model"]) == {"gpt-obj", "profile-model"}

    def test_separate_runs_are_both_counted(self) -> None:
        from agentegrity.adapters.openai_agents import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(profile=_profile(), stream_from_env=False)
        agent = SimpleNamespace(name="a", model="gpt-5.5")
        for response_id in ("r1", "r2"):
            shared = self._sdk_usage(0, 0, 0, 0, requests=0)
            self._run(adapter, [(agent, self._response(response_id, 10, 0, 1, 0))], shared)
        assert _usage(adapter)["input_tokens"] == 20


@_needs("langchain_core")
class TestLangChain:
    @staticmethod
    def _result(usage: dict[str, Any] | None, model: str | None = "claude-x",
                llm_output: dict[str, Any] | None = None) -> Any:
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult

        message = AIMessage(content="ok", usage_metadata=usage,
                            response_metadata={"model_name": model} if model else {})
        return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output)

    @staticmethod
    def _adapter() -> Any:
        from agentegrity.adapters.langchain import LangChainAdapter

        return LangChainAdapter(profile=_profile(), stream_from_env=False)

    def test_usage_metadata_is_already_inclusive(self) -> None:
        from uuid import uuid4

        adapter = self._adapter()
        handler = adapter.create_callback_handler()
        usage = {"input_tokens": 1100, "output_tokens": 40, "total_tokens": 1140,
                 "input_token_details": {"cache_read": 900, "cache_creation": 100},
                 "output_token_details": {"reasoning": 12}}
        handler.on_llm_end(self._result(usage), run_id=uuid4())
        result = _usage(adapter)
        assert (result["input_tokens"], result["cache_read_tokens"]) == (1100, 900)
        assert (result["cache_write_tokens"], result["reasoning_tokens"]) == (100, 12)
        assert list(result["by_model"]) == ["claude-x"]

    def test_split_cache_writes_and_tier_prefixed_keys_are_counted(self) -> None:
        from uuid import uuid4

        adapter = self._adapter()
        handler = adapter.create_callback_handler()
        usage = {"input_tokens": 500, "output_tokens": 5, "total_tokens": 505,
                 "input_token_details": {"cache_creation": 0, "ephemeral_5m_input_tokens": 30,
                                         "ephemeral_1h_input_tokens": 20,
                                         "priority_cache_read": 300},
                 "output_token_details": {"priority_reasoning": 3}}
        handler.on_llm_end(self._result(usage), run_id=uuid4())
        result = _usage(adapter)
        assert (result["cache_write_tokens"], result["cache_read_tokens"]) == (50, 300)
        assert result["reasoning_tokens"] == 3

    def test_the_same_run_reported_twice_counts_once(self) -> None:
        from uuid import uuid4

        adapter = self._adapter()
        handler = adapter.create_callback_handler()
        run_id = uuid4()
        usage = {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}
        handler.on_llm_end(self._result(usage), run_id=run_id)
        handler.on_llm_end(self._result(usage), run_id=run_id)
        result = _usage(adapter)
        assert (result["requests"], "cache_read_tokens" in result) == (1, False)

    def test_model_comes_from_the_start_event_when_the_response_has_none(self) -> None:
        from uuid import uuid4

        adapter = self._adapter()
        handler = adapter.create_callback_handler()
        run_id = uuid4()
        handler.on_chat_model_start({}, [[]], run_id=run_id,
                                    metadata={"ls_model_name": "gpt-start"})
        handler.on_llm_end(self._result({"input_tokens": 1, "output_tokens": 1,
                                         "total_tokens": 2}, model=None), run_id=run_id)
        assert list(_usage(adapter)["by_model"]) == ["gpt-start"]

    def test_legacy_llm_output_is_used_when_messages_carry_no_usage(self) -> None:
        from uuid import uuid4

        adapter = self._adapter()
        handler = adapter.create_callback_handler()
        handler.on_llm_end(self._result(None, llm_output={
            "token_usage": {"prompt_tokens": 70, "completion_tokens": 7},
            "model_name": "legacy-model"}), run_id=uuid4())
        result = _usage(adapter)
        assert (result["input_tokens"], result["output_tokens"]) == (70, 7)


@_needs("crewai")
class TestCrewAI:
    @staticmethod
    def _event(call_id: str, usage: dict[str, Any] | None, model: str = "gpt-5.5") -> Any:
        from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallType

        return LLMCallCompletedEvent(model=model, call_id=call_id, usage=usage, response="ok",
                                     call_type=LLMCallType.LLM_CALL)

    @staticmethod
    def _adapter() -> Any:
        from agentegrity.adapters.crewai import CrewAIAdapter

        return CrewAIAdapter(profile=_profile(), stream_from_env=False)

    def test_provider_dicts_are_normalized_to_include_the_cache(self) -> None:
        adapter = self._adapter()
        adapter._record_llm_call(self._event("c1", {
            "input_tokens": 10, "cache_read_input_tokens": 90,
            "cache_creation_input_tokens": 5, "output_tokens": 3}, model="claude-x"))
        adapter._record_llm_call(self._event("c2", {
            "prompt_tokens": 100, "cached_prompt_tokens": 60, "completion_tokens": 9,
            "reasoning_tokens": 2}))
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["cache_read_tokens"]) == (205, 150)
        assert (usage["cache_write_tokens"], usage["reasoning_tokens"]) == (5, 2)
        assert set(usage["by_model"]) == {"claude-x", "gpt-5.5"}

    def test_a_call_without_usage_is_skipped_and_repeats_count_once(self) -> None:
        adapter = self._adapter()
        adapter._record_llm_call(self._event("c1", None))
        assert "usage" not in adapter.get_summary()
        event = self._event("c2", {"prompt_tokens": 5, "completion_tokens": 1})
        adapter._record_llm_call(event)
        adapter._record_llm_call(event)
        assert _usage(adapter)["requests"] == 1

    def test_subscribe_listens_for_llm_calls(self) -> None:
        from crewai.events import LLMCallCompletedEvent, crewai_event_bus

        adapter = self._adapter()
        with crewai_event_bus.scoped_handlers():
            adapter.subscribe()
            handlers = crewai_event_bus._sync_handlers.get(LLMCallCompletedEvent, set())
            assert handlers


@_needs("autogen_core")
class TestAutoGen:
    @pytest.fixture
    def adapter(self) -> Any:
        from agentegrity.adapters.autogen import AutoGenAdapter

        adapter = AutoGenAdapter(profile=_profile())
        adapter._capture_usage()
        yield adapter
        adapter.close()

    @staticmethod
    def _log(event: Any) -> None:
        import logging

        from autogen_core import EVENT_LOGGER_NAME

        logging.getLogger(EVENT_LOGGER_NAME).info(event)

    def test_openai_calls_are_counted_with_their_cache(self, adapter: Any) -> None:
        from autogen_core.logging import LLMCallEvent

        self._log(LLMCallEvent(messages=[], prompt_tokens=100, completion_tokens=8, response={
            "model": "gpt-5.5", "usage": {
                "prompt_tokens": 100, "completion_tokens": 8,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 3}}}))
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["cache_read_tokens"]) == (100, 40)
        assert usage["reasoning_tokens"] == 3
        assert list(usage["by_model"]) == ["gpt-5.5"]

    def test_anthropic_cache_is_added_to_input(self, adapter: Any) -> None:
        from autogen_core.logging import LLMCallEvent

        self._log(LLMCallEvent(messages=[], prompt_tokens=10, completion_tokens=2, response={
            "model": "claude-x", "usage": {
                "input_tokens": 10, "output_tokens": 2,
                "cache_read_input_tokens": 500, "cache_creation_input_tokens": 50}}))
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["cache_write_tokens"]) == (560, 50)

    def test_streamed_calls_are_counted(self, adapter: Any) -> None:
        from autogen_core.logging import LLMStreamEndEvent

        self._log(LLMStreamEndEvent(response={"content": "x"}, prompt_tokens=7,
                                    completion_tokens=3))
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["requests"]) == (7, 1)
        assert list(usage["by_model"]) == ["profile-model"]

    def test_user_logging_output_is_unchanged(self, adapter: Any) -> None:
        import logging

        from autogen_core.logging import LLMCallEvent

        seen: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                seen.append(record)

        root = logging.getLogger()
        handler = _Capture()
        root.addHandler(handler)
        try:
            self._log(LLMCallEvent(messages=[], prompt_tokens=1, completion_tokens=1,
                                   response={}))
        finally:
            root.removeHandler(handler)
        assert not [r for r in seen if r.name == "autogen_core.events"]
        assert _usage(adapter)["requests"] == 1

    def test_every_live_adapter_gets_usage(self, adapter: Any) -> None:
        import logging

        from autogen_core import EVENT_LOGGER_NAME
        from autogen_core.logging import LLMCallEvent

        from agentegrity.adapters.autogen import AutoGenAdapter

        events_logger = logging.getLogger(EVENT_LOGGER_NAME)
        second = AutoGenAdapter(profile=_profile())
        second._capture_usage()
        try:
            self._log(LLMCallEvent(messages=[], prompt_tokens=4, completion_tokens=1,
                                   response={}))
            assert _usage(adapter)["input_tokens"] == _usage(second)["input_tokens"] == 4
        finally:
            second.close()
        assert events_logger.isEnabledFor(logging.INFO)  # the first adapter still captures

    def test_close_stops_capturing(self) -> None:
        import logging

        from autogen_core import EVENT_LOGGER_NAME

        from agentegrity.adapters.autogen import AutoGenAdapter

        events_logger = logging.getLogger(EVENT_LOGGER_NAME)
        before = (events_logger.level, list(events_logger.filters))
        adapter = AutoGenAdapter(profile=_profile())
        adapter._capture_usage()
        adapter._capture_usage()
        adapter.close()
        assert (events_logger.level, list(events_logger.filters)) == before


@_needs("agno")
class TestAgno:
    @staticmethod
    def _metrics(details: dict[str, list[Any]] | None = None, **counts: int) -> Any:
        from agno.metrics import RunMetrics

        metrics = RunMetrics(**counts)
        metrics.details = details
        return metrics

    @staticmethod
    def _model(provider: str, model_id: str, **counts: int) -> Any:
        from agno.metrics import ModelMetrics

        return ModelMetrics(id=model_id, provider=provider, **counts)

    @staticmethod
    def _post_hook(adapter: Any) -> Any:
        target = SimpleNamespace(name="agent", pre_hooks=None, post_hooks=None, tool_hooks=None)
        adapter._attach_hooks(target, is_team_member=False)
        return target.post_hooks[0]

    @staticmethod
    def _adapter() -> Any:
        from agentegrity.adapters.agno import AgnoAdapter

        return AgnoAdapter(profile=_profile(), stream_from_env=False)

    def _run(self, run_id: str, metrics: Any, model: str = "gpt-5.5",
             provider: str = "OpenAI") -> Any:
        return SimpleNamespace(run_id=run_id, model=model, model_provider=provider,
                               metrics=metrics, content="done")

    def test_per_model_details_are_normalized(self) -> None:
        adapter = self._adapter()
        details = {
            "model": [self._model("Anthropic", "claude-x", input_tokens=10, output_tokens=5,
                                  cache_read_tokens=800, cache_write_tokens=40)],
            "memory_model": [self._model("Google", "gemini-x", input_tokens=100,
                                         output_tokens=20, cache_read_tokens=30,
                                         reasoning_tokens=7)],
        }
        self._post_hook(adapter)(self._run("run-1", self._metrics(details)))
        usage = _usage(adapter)
        assert usage["by_model"]["claude-x"]["input_tokens"] == 850
        assert usage["by_model"]["gemini-x"]["input_tokens"] == 100
        assert usage["by_model"]["gemini-x"]["output_tokens"] == 27
        stop = next(e for e in adapter.events if e.event_type == "stop")
        assert stop.data["usage"]["input_tokens"] == 950

    def test_totals_are_used_when_there_are_no_details(self) -> None:
        adapter = self._adapter()
        self._post_hook(adapter)(self._run("run-1", self._metrics(
            input_tokens=300, output_tokens=30, cache_read_tokens=100)))
        usage = _usage(adapter)
        assert (usage["input_tokens"], list(usage["by_model"])) == (300, ["gpt-5.5"])

    def test_calls_merged_after_the_post_hook_are_read_at_close(self) -> None:
        adapter = self._adapter()
        metrics = self._metrics(input_tokens=300, output_tokens=30)
        self._post_hook(adapter)(self._run("run-1", metrics))
        metrics.input_tokens += 50  # memory / summary model calls merged later
        adapter.close()
        assert _usage(adapter)["input_tokens"] == 350

    def test_a_run_without_metrics_records_nothing(self) -> None:
        adapter = self._adapter()
        self._post_hook(adapter)(self._run("run-1", None))
        assert "usage" not in adapter.get_summary()


@_needs("google.adk")
class TestGoogleADK:
    @staticmethod
    def _agent() -> Any:
        return SimpleNamespace(name="a", model="gemini-cfg", before_agent_callback=None,
                               after_agent_callback=None, before_tool_callback=None,
                               after_tool_callback=None, after_model_callback=None,
                               sub_agents=[])

    @staticmethod
    def _response(partial: bool | None = None, model: str | None = "gemini-x",
                  **counts: int) -> Any:
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types

        return LlmResponse(partial=partial, model_version=model,
                           usage_metadata=types.GenerateContentResponseUsageMetadata(**counts))

    @staticmethod
    def _adapter() -> Any:
        from agentegrity.adapters.google_adk import GoogleADKAdapter

        return GoogleADKAdapter(profile=_profile())

    def test_counts_are_normalized_and_partials_skipped(self) -> None:
        adapter, agent = self._adapter(), self._agent()
        adapter.instrument(agent)
        partial = self._response(partial=True, prompt_token_count=1000,
                                 candidates_token_count=1)
        final = self._response(prompt_token_count=1000, cached_content_token_count=600,
                               tool_use_prompt_token_count=50, candidates_token_count=40,
                               thoughts_token_count=25)
        for response in (partial, partial, final):
            agent.after_model_callback(callback_context=None, llm_response=response)
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["cache_read_tokens"]) == (1050, 600)
        assert (usage["output_tokens"], usage["reasoning_tokens"]) == (65, 25)
        assert (usage["requests"], list(usage["by_model"])) == (1, ["gemini-x"])

    def test_user_model_callback_still_runs_and_decides(self) -> None:
        adapter, agent = self._adapter(), self._agent()
        replacement = object()
        agent.after_model_callback = lambda callback_context, llm_response: replacement
        adapter.instrument(agent)
        result = agent.after_model_callback(
            callback_context=None,
            llm_response=self._response(prompt_token_count=5, candidates_token_count=1))
        assert result is replacement
        assert _usage(adapter)["input_tokens"] == 5

    def test_model_falls_back_to_the_agent(self) -> None:
        adapter, agent = self._adapter(), self._agent()
        adapter.instrument(agent)
        agent.after_model_callback(
            callback_context=None,
            llm_response=self._response(model=None, prompt_token_count=5,
                                        candidates_token_count=1))
        assert list(_usage(adapter)["by_model"]) == ["gemini-cfg"]

    def test_agents_without_a_model_callback_are_left_alone(self) -> None:
        adapter, agent = self._adapter(), self._agent()
        del agent.after_model_callback
        adapter.instrument(agent)
        assert not hasattr(agent, "after_model_callback")


class TestBedrockAgentsTraces:
    @staticmethod
    def _adapter() -> Any:
        from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter

        return BedrockAgentsAdapter(profile=_profile())

    @staticmethod
    def _trace(kind: str, part: dict[str, Any]) -> dict[str, Any]:
        return {"trace": {"trace": {kind: part}}}

    def test_every_model_invocation_is_counted(self) -> None:
        from agentegrity.adapters.bedrock_agents import _handle_stream_event

        adapter = self._adapter()
        events = [
            self._trace("routingClassifierTrace", {"modelInvocationInput": {
                "traceId": "t0", "foundationModel": "anthropic.claude-x"}}),
            self._trace("routingClassifierTrace", {"modelInvocationOutput": {
                "traceId": "t0", "metadata": {"usage": {"inputTokens": 30, "outputTokens": 2}}}}),
            self._trace("orchestrationTrace", {"modelInvocationOutput": {
                "traceId": "t1", "metadata": {"usage": {"inputTokens": 500, "outputTokens": 40}}}}),
            self._trace("preProcessingTrace", {"modelInvocationOutput": {
                "traceId": "t2", "metadata": {"usage": {"inputTokens": 100, "outputTokens": 5}}}}),
            self._trace("orchestrationTrace", {"modelInvocationOutput": {
                "traceId": "t1", "metadata": {"usage": {"inputTokens": 500, "outputTokens": 40}}}}),
        ]
        for event in events:
            _handle_stream_event(adapter, event)
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["output_tokens"], usage["requests"]) == (630, 47, 3)
        assert usage["by_model"]["anthropic.claude-x"]["input_tokens"] == 30
        assert usage["by_model"]["profile-model"]["input_tokens"] == 600
        assert "cache_read_tokens" not in usage


@_needs("strands")
class TestBedrockStrands:
    @staticmethod
    def _event(**usage: int) -> Any:
        from strands.telemetry.metrics import AgentInvocation, EventLoopMetrics

        metrics = EventLoopMetrics()
        metrics.agent_invocations.append(AgentInvocation(cycles=[object(), object()],
                                                         usage=usage))  # type: ignore[arg-type]
        agent = SimpleNamespace(model=SimpleNamespace(config={"model_id": "claude-on-bedrock"}))
        return SimpleNamespace(agent=agent, result=SimpleNamespace(metrics=metrics))

    @staticmethod
    def _provider() -> tuple[Any, Any]:
        from agentegrity.adapters.bedrock_agents import BedrockAgentsAdapter, _StrandsHookProvider

        adapter = BedrockAgentsAdapter(profile=_profile())
        return adapter, _StrandsHookProvider(adapter)

    def test_cache_reported_beside_input_is_added(self) -> None:
        adapter, provider = self._provider()
        provider._on_after_invocation(self._event(
            inputTokens=10, outputTokens=5, totalTokens=915,
            cacheReadInputTokens=800, cacheWriteInputTokens=100))
        usage = _usage(adapter)
        assert (usage["input_tokens"], usage["cache_read_tokens"]) == (910, 800)
        assert (usage["requests"], list(usage["by_model"])) == (2, ["claude-on-bedrock"])
        stop = next(e for e in adapter.events if e.event_type == "stop")
        assert stop.data["usage"]["input_tokens"] == 910

    def test_cache_already_inside_input_is_not_added_twice(self) -> None:
        adapter, provider = self._provider()
        provider._on_after_invocation(self._event(
            inputTokens=900, outputTokens=5, totalTokens=905, cacheReadInputTokens=800))
        assert _usage(adapter)["input_tokens"] == 900
