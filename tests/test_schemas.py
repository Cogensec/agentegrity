"""Validate real Python exporter payloads against the JSON Schemas in
``schemas/exporter/``. Catches drift between the schemas and the
Python dataclass ``to_dict`` outputs at CI time.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip("jsonschema")
pytest.importorskip("referencing")
from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402

from agentegrity.adapters.base import FrameworkEvent, _BaseAdapter  # noqa: E402
from agentegrity.core.profile import AgentProfile  # noqa: E402
from agentegrity.core.usage import TokenUsage  # noqa: E402

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas" / "exporter"


def _load(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / name).read_text())


@pytest.fixture(scope="module")
def registry() -> Registry:
    """Registry that resolves the relative `common.json` $refs."""
    common = _load("common.json")
    resource = Resource.from_contents(common)
    return Registry().with_resource(uri="common.json", resource=resource)


@pytest.fixture(scope="module")
def session_start_validator(registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(_load("session_start.json"), registry=registry)


@pytest.fixture(scope="module")
def event_validator(registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(_load("event.json"), registry=registry)


@pytest.fixture(scope="module")
def session_end_validator(registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(_load("session_end.json"), registry=registry)


class _TestAdapter(_BaseAdapter):
    _name = "test"


def _drive_adapter() -> _TestAdapter:
    a = _TestAdapter(profile=AgentProfile.default())
    asyncio.run(
        a.on_event(
            "pre_tool_use", {"tool_name": "Read", "tool_input": {"p": "x"}}
        )
    )
    asyncio.run(a.on_event("post_tool_use", {"tool_name": "Read", "tool_response": "ok"}))
    asyncio.run(a.on_event("stop", {"output": "done"}))
    return a


def test_session_start_payload_matches_schema(
    session_start_validator: Draft202012Validator,
) -> None:
    a = _drive_adapter()
    payload = {
        "session_id": a.session_id,
        "adapter_name": a.name,
        "profile": a.profile.to_dict(),
    }
    session_start_validator.validate(payload)


def test_event_payloads_match_schema(
    event_validator: Draft202012Validator,
) -> None:
    a = _drive_adapter()
    assert a.events
    for ev in a.events:
        event_validator.validate(
            {"session_id": a.session_id, "event": ev.to_dict()}
        )


def test_session_end_payload_matches_schema(
    session_end_validator: Draft202012Validator,
) -> None:
    a = _drive_adapter()
    payload = {"session_id": a.session_id, "summary": a.get_summary()}
    session_end_validator.validate(payload)


def test_schemas_are_themselves_valid_draft202012() -> None:
    for name in ("common.json", "session_start.json", "event.json", "session_end.json"):
        schema = _load(name)
        Draft202012Validator.check_schema(schema)


def test_openapi_yaml_parses() -> None:
    yaml = pytest.importorskip("yaml")
    path = SCHEMAS_DIR.parent / "openapi.yaml"
    doc = yaml.safe_load(path.read_text())
    assert doc["openapi"].startswith("3.")
    assert "/sessions" in doc["paths"]
    assert "/sessions/{sessionId}/events" in doc["paths"]
    assert "/sessions/{sessionId}/end" in doc["paths"]


# ---------------------------------------------------------------------------
# Event types: the schema accepts any snake_case type, so a consumer that
# validates against an older copy never rejects a newer event, and lists the
# known ones in ``examples``. These tests keep that list equal to what the
# SDK actually emits.
# ---------------------------------------------------------------------------

_SRC = Path(__file__).parent.parent / "src" / "agentegrity"
_OVERFLOW = "<channel>_overflow"


def _event_type_schema() -> dict[str, Any]:
    event: dict[str, Any] = _load("common.json")["$defs"]["FrameworkEvent"]
    schema: dict[str, Any] = event["properties"]["event_type"]
    return schema


def _emitted_event_types() -> set[str]:
    """Every event type passed to ``_emit_event`` anywhere in the SDK."""
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_emit_event" and node.args):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                literal = "".join(
                    v.value for v in arg.values if isinstance(v, ast.Constant))
                assert literal == "_overflow", f"{path}: undocumented dynamic event type"
                found.add(_OVERFLOW)
            else:
                raise AssertionError(f"{path}:{node.lineno}: event type is not a literal")
    return found


def test_known_event_types_are_exactly_the_emitted_ones() -> None:
    assert set(_event_type_schema()["examples"]) == _emitted_event_types()


@pytest.mark.parametrize("event_type", sorted(
    t.replace(_OVERFLOW, "tool_outputs_overflow") for t in _emitted_event_types()))
def test_every_emitted_event_type_validates(
    event_validator: Draft202012Validator, event_type: str,
) -> None:
    event = FrameworkEvent(event_type=event_type, adapter_name="test", data={})
    event_validator.validate({"session_id": "0" * 32, "event": event.to_dict()})


def test_a_future_event_type_validates_and_a_malformed_one_does_not(
    event_validator: Draft202012Validator,
) -> None:
    def payload(event_type: str) -> dict[str, Any]:
        event = FrameworkEvent(event_type=event_type, adapter_name="test", data={})
        return {"session_id": "0" * 32, "event": event.to_dict()}

    event_validator.validate(payload("introduced_later"))
    assert not event_validator.is_valid(payload("Not A Type"))


def test_usage_from_a_later_release_validates(
    session_end_validator: Draft202012Validator,
) -> None:
    a = _drive_adapter()
    a.record_usage("k1", "m", TokenUsage(input_tokens=10, output_tokens=2),
                   source="provider_response")
    summary = a.get_summary()
    summary["usage"]["sources"].append("introduced_later")
    summary["usage"]["field_added_later"] = 1
    summary["usage"]["by_model"]["m"]["field_added_later"] = 1
    session_end_validator.validate({"session_id": a.session_id, "summary": summary})
