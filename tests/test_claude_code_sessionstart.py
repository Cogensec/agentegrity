"""Tests for the Claude Code plugin's SessionStart hook.

The hook is a standalone script under integrations/claude-code/hooks,
loaded via importlib. Semantics under test: it warns loudly (via the
``systemMessage`` field) when the library does not import or the plugin
is disabled, stays silent on a healthy install, and never raises.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "integrations"
    / "claude-code"
    / "hooks"
    / "sessionstart.py"
)


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("cc_sessionstart", _HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestBuildStatus:
    def test_disabled_warns(self, hook):
        out = hook.build_status({"AGENTEGRITY_CC_DISABLED": "1"})
        assert out is not None
        assert "disabled" in out["systemMessage"].lower()
        assert "not being verified" in out["systemMessage"].lower()

    def test_missing_library_warns_with_fix(self, hook, monkeypatch):
        monkeypatch.setattr(hook, "_library_usable", lambda: False)
        out = hook.build_status({})
        assert out is not None
        msg = out["systemMessage"]
        assert "not being verified" in msg.lower()
        assert "pip install" in msg
        assert "/agentegrity-init" in msg

    def test_missing_library_reports_configured_mode(self, hook, monkeypatch):
        monkeypatch.setattr(hook, "_library_usable", lambda: False)
        out = hook.build_status({"AGENTEGRITY_CC_MODE": "alert"})
        assert "alert" in out["systemMessage"]

    def test_healthy_install_is_silent(self, hook, monkeypatch):
        monkeypatch.setattr(hook, "_library_usable", lambda: True)
        assert hook.build_status({}) is None

    def test_disabled_takes_precedence_over_usable(self, hook, monkeypatch):
        # Disabled is reported even when the library would import — the
        # user still needs to know the session is unprotected.
        monkeypatch.setattr(hook, "_library_usable", lambda: True)
        out = hook.build_status({"AGENTEGRITY_CC_DISABLED": "1"})
        assert out is not None
        assert "disabled" in out["systemMessage"].lower()


class TestLibraryProbe:
    def test_probe_true_for_this_interpreter(self, hook):
        # The test runner's interpreter has agentegrity importable, so
        # the real subprocess probe must agree.
        assert hook._library_usable() is True

    def test_probe_false_on_subprocess_error(self, hook, monkeypatch):
        import subprocess

        def boom(*args, **kwargs):
            raise OSError("cannot spawn")

        monkeypatch.setattr(subprocess, "run", boom)
        assert hook._library_usable() is False
