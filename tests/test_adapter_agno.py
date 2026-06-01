"""Live test for AgnoAdapter.

The conformance suite drives ``on_event`` directly. This module
exercises the framework-specific glue against the real Agno hook
machinery:

* tool_hooks run through a real ``FunctionCall.execute()`` so we prove
  the middleware signature (``hook(name, func, arguments)``) and the
  success / failure branches map to the right canonical events.
* pre/post hooks are attached to a real ``Agent`` instance and invoked
  with the argument shapes Agno passes (``run_input`` / ``run_output``).

Skipped when ``agno`` is not installed.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("agno")

from agno.tools.function import Function, FunctionCall

from agentegrity.adapters.agno import AgnoAdapter
from agentegrity.core.profile import AgentProfile


def _profile() -> AgentProfile:
    return AgentProfile.default()


class _FakeRunInput:
    def __init__(self, content: str) -> None:
        self.input_content = content


class _FakeRunOutput:
    def __init__(self, content: str) -> None:
        self._content = content

    def get_content_as_string(self) -> str:
        return self._content


class _HookTarget:
    """Stand-in for an Agno Agent/Team: just carries the hook lists."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.pre_hooks: list[Any] | None = None
        self.post_hooks: list[Any] | None = None
        self.tool_hooks: list[Any] | None = None


def _instrument_target(adapter: AgnoAdapter, target: _HookTarget, *, member: bool) -> None:
    adapter._attach_hooks(target, is_team_member=member)


def test_attach_hooks_appends_to_all_three_lists() -> None:
    adapter = AgnoAdapter(profile=_profile())
    target = _HookTarget("solo")
    _instrument_target(adapter, target, member=False)
    assert target.pre_hooks is not None and len(target.pre_hooks) == 1
    assert target.post_hooks is not None and len(target.post_hooks) == 1
    assert target.tool_hooks is not None and len(target.tool_hooks) == 1


def test_attach_hooks_chains_onto_existing_user_hooks() -> None:
    adapter = AgnoAdapter(profile=_profile())
    target = _HookTarget("solo")
    sentinel = lambda: None  # noqa: E731
    target.pre_hooks = [sentinel]
    _instrument_target(adapter, target, member=False)
    # User hook preserved, ours appended.
    assert target.pre_hooks[0] is sentinel
    assert len(target.pre_hooks) == 2


def test_standalone_pre_post_emit_prompt_and_stop() -> None:
    adapter = AgnoAdapter(profile=_profile())
    target = _HookTarget("solo")
    _instrument_target(adapter, target, member=False)

    target.pre_hooks[0](_FakeRunInput("hello world"))
    target.post_hooks[0](_FakeRunOutput("the answer"))

    types = [e.event_type for e in adapter.events]
    assert types == ["user_prompt_submit", "stop"]
    assert adapter.attestation_chain.verify_chain()


def test_team_member_pre_post_emit_subagent_events() -> None:
    adapter = AgnoAdapter(profile=_profile())
    member = _HookTarget("worker-1")
    _instrument_target(adapter, member, member=True)

    member.pre_hooks[0](_FakeRunInput("subtask"))
    member.post_hooks[0](_FakeRunOutput("subresult"))

    types = [e.event_type for e in adapter.events]
    assert types == ["subagent_start", "subagent_stop"]
    start = adapter.events[0]
    assert start.data["agent_id"] == "worker-1"


def _run_tool(adapter: AgnoAdapter, target: _HookTarget, fn: Any, arguments: dict[str, Any]) -> Any:
    """Register the adapter's tool_hook on a real Agno Function and execute it."""
    f = Function.from_callable(fn)
    f.tool_hooks = list(target.tool_hooks)
    f._agent = None
    return FunctionCall(function=f, arguments=arguments).execute()


def test_tool_hook_success_maps_to_pre_and_post() -> None:
    adapter = AgnoAdapter(profile=_profile())
    target = _HookTarget("solo")
    _instrument_target(adapter, target, member=False)

    def add(a: int, b: int) -> int:
        return a + b

    res = _run_tool(adapter, target, add, {"a": 2, "b": 3})
    assert res.status == "success" and res.result == 5

    types = [e.event_type for e in adapter.events]
    assert types == ["pre_tool_use", "post_tool_use"]
    pre = adapter.events[0]
    assert pre.data["tool_name"] == "add"
    assert pre.data["tool_input"] == {"a": 2, "b": 3}


def test_tool_hook_failure_maps_to_post_tool_use_failure() -> None:
    adapter = AgnoAdapter(profile=_profile())
    target = _HookTarget("solo")
    _instrument_target(adapter, target, member=False)

    def boom(x: int) -> int:
        raise ValueError("kaboom")

    res = _run_tool(adapter, target, boom, {"x": 1})
    assert res.status == "failure"

    types = [e.event_type for e in adapter.events]
    assert types == ["pre_tool_use", "post_tool_use_failure"]
    failure = adapter.events[-1]
    assert failure.data["tool_name"] == "boom"
    assert "kaboom" in failure.data["error"]


def test_enforce_true_emits_warning() -> None:
    with pytest.warns(UserWarning, match="observation-only"):
        AgnoAdapter(profile=_profile(), enforce=True)


def test_instrument_team_marks_members_as_subagents() -> None:
    """instrument_team attaches leader hooks to the team and member hooks
    to each statically-listed member."""

    class _FakeTeam:
        def __init__(self, members: list[_HookTarget]) -> None:
            self.name = "team"
            self.members = members
            self.pre_hooks: list[Any] | None = None
            self.post_hooks: list[Any] | None = None
            self.tool_hooks: list[Any] | None = None

    m1 = _HookTarget("m1")
    m2 = _HookTarget("m2")
    team = _FakeTeam([m1, m2])

    adapter = AgnoAdapter(profile=_profile())
    adapter.instrument_team(team)  # type: ignore[arg-type]

    # Leader fires top-level events.
    team.pre_hooks[0](_FakeRunInput("top task"))
    # Member fires subagent events.
    m1.pre_hooks[0](_FakeRunInput("sub task"))
    m1.post_hooks[0](_FakeRunOutput("sub done"))
    team.post_hooks[0](_FakeRunOutput("top done"))

    types = [e.event_type for e in adapter.events]
    assert types == [
        "user_prompt_submit",
        "subagent_start",
        "subagent_stop",
        "stop",
    ]
