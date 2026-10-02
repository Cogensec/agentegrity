"""Argument-level classification of tool calls.

Name-based policy (GOV-001, :class:`ToolSequenceDetector` categories)
cannot see what a generic shell tool is doing: ``cat ~/.aws/credentials``
and ``ls`` are both just ``Bash``. This module reads the call's
arguments and tags it with behavioral categories that the layers act on.

Shell commands are tokenized the way the shell would see them
(:mod:`shlex` in POSIX mode collapses quotes, so ``c''at`` is ``cat``)
and split into pipeline segments, so a downloader piped into an
interpreter is recognised by structure rather than by raw substring.
Command substitution and ``$IFS`` splicing cannot be resolved
statically; they are tagged :attr:`ToolCallCategory.OBFUSCATED_COMMAND`
instead. This is a floor for plainly-written commands, not a sandbox:
anything the shell computes at runtime is outside its reach.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from enum import Enum
from typing import Any


class ToolCallCategory(str, Enum):
    """Behavioral category of a single tool call."""

    READS_SENSITIVE = "reads_sensitive"
    SENDS_EXTERNAL = "sends_external"
    REMOTE_CODE_EXEC = "remote_code_exec"
    LOG_TAMPER = "log_tamper"
    OBFUSCATED_COMMAND = "obfuscated_command"


_COMMAND_KEYS = ("command", "cmd", "script", "commands")
_PATH_KEYS = ("file_path", "path", "filename", "notebook_path")
_WRITE_TOOL = re.compile(r"write|edit|create|delete|remove|move|rename|mkdir", re.IGNORECASE)

_SENSITIVE_PATH = re.compile(
    "|".join(
        (
            r"(?:^|/)\.aws/credentials$",
            r"(?:^|/)\.ssh/id_[^/]+(?<!\.pub)$",
            r"(?:^|/)\.env(?:\.(?!example$|sample$|template$|dist$)[^/]+)?$",
            r"(?:^|/)\.netrc$",
            r"(?:^|/)\.git-credentials$",
            r"(?:^|/)\.docker/config\.json$",
            r"(?:^|/)\.kube/config$",
            r"(?:^|/)\.config/gcloud/credentials\.db$",
            r"(?:^|/)application_default_credentials\.json$",
            r"^/etc/shadow$",
            r"^/proc/[^/]+/environ$",
            r"\.(?:key|p12|pfx)$",
        )
    )
)
_LOG_TARGET = re.compile(
    r"\.log(?:\.\d+)?$|^/var/log(?:/|$)|(?:^|/)\.(?:bash_|zsh_|python_|sh_)?history$"
)
_HISTORY_DISABLE = re.compile(r"^HIST(?:FILE=/dev/null|SIZE=0|FILESIZE=0)$")
_REMOTE_SPEC = re.compile(r"^(?:[\w.-]+@)?[\w.-]+:")

_SEGMENT_OPS = {"|", "|&", "||", "&&", ";", "&", "(", ")", "<("}
_PIPE_OPS = {"|", "|&"}
_WRAPPERS = {"sudo", "doas", "nohup", "time", "command", "exec", "nice"}
_DOWNLOADERS = {"curl", "wget", "fetch"}
_INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish",
    "python", "python2", "python3", "perl", "ruby", "node", "php", "pwsh",
}
_RAW_SOCKET_TOOLS = {"nc", "ncat", "netcat", "socat", "telnet"}
_REMOTE_COPY_TOOLS = {"scp", "sftp", "rsync"}
_LOG_DESTROYERS = {"rm", "shred", "truncate", "unlink"}
_CURL_SEND_FLAGS = {"-d", "-F", "-T", "--form", "--form-string", "--upload-file", "--json"}
_SEND_METHODS = {"POST", "PUT", "PATCH"}
# Substitution that fetches code and hands it to an interpreter:
# `sh -c "$(curl ...)"`, `eval "$(wget ...)"`.
_SUBSTITUTED_FETCH = re.compile(
    r"(?:\beval\b|\b(?:ba|z|da|k)?sh\s+-c\b)[^\n]*?(?:\$\(|`)\s*(?:curl|wget)\b"
)


def classify_tool_call(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
) -> frozenset[ToolCallCategory]:
    """Tag a tool call with the behavioral categories its arguments show."""
    if not isinstance(arguments, Mapping):
        return frozenset()
    categories: set[ToolCallCategory] = set()
    for key in _COMMAND_KEYS:
        value = arguments.get(key)
        if isinstance(value, list):
            value = "\n".join(str(v) for v in value)
        if isinstance(value, str) and value.strip():
            categories |= _classify_shell(value)
    if not _WRITE_TOOL.search(tool_name or ""):
        for key in _PATH_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and _is_sensitive_path(value):
                categories.add(ToolCallCategory.READS_SENSITIVE)
    return frozenset(categories)


def _classify_shell(command: str) -> set[ToolCallCategory]:
    """Classify one shell command string."""
    tokens = _tokenize(command)
    segments, connectors = _split_segments(tokens)
    categories: set[ToolCallCategory] = set()
    for segment in segments:
        categories |= _classify_segment(segment)
    if _pipes_fetched_code_to_interpreter(segments, connectors):
        categories.add(ToolCallCategory.REMOTE_CODE_EXEC)
    if _SUBSTITUTED_FETCH.search(" ".join(tokens)):
        categories.add(ToolCallCategory.REMOTE_CODE_EXEC)
    return categories


def _tokenize(command: str) -> list[str]:
    """Split like a POSIX shell (quotes collapsed, operators separated)."""
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        # Unbalanced quotes: the shell would reject it too, but still
        # classify what is there rather than going blind.
        return command.split()


def _split_segments(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    """Split tokens into simple commands; connectors[i] joins segment i and i+1."""
    segments: list[list[str]] = [[]]
    connectors: list[str] = []
    for token in tokens:
        if token in _SEGMENT_OPS:
            segments.append([])
            connectors.append(token)
        else:
            segments[-1].append(token)
    return segments, connectors


def _executable(segment: list[str]) -> tuple[str, list[str]]:
    """Return (executable basename, its arguments), skipping assignments and wrappers."""
    index = 0
    while index < len(segment):
        token = segment[index]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            index += 1
        elif os.path.basename(token) in _WRAPPERS:
            index += 1
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
        else:
            return os.path.basename(token), segment[index + 1:]
    return "", []


def _classify_segment(segment: list[str]) -> set[ToolCallCategory]:
    """Classify one simple command (no pipes or separators)."""
    categories: set[ToolCallCategory] = set()
    if not segment:
        return categories
    exe, args = _executable(segment)
    redirect_targets = {
        segment[i + 1] for i, t in enumerate(segment[:-1]) if t in (">", ">>", ">|")
    }
    overwrite_targets = {
        segment[i + 1] for i, t in enumerate(segment[:-1]) if t in (">", ">|")
    }

    if exe.startswith(("$", "`")) or any("$IFS" in t or "${IFS" in t for t in segment):
        categories.add(ToolCallCategory.OBFUSCATED_COMMAND)

    if exe == "printenv" or (exe == "env" and not _env_runs_command(args)):
        categories.add(ToolCallCategory.READS_SENSITIVE)
    if any(_is_sensitive_path(t) for t in segment if t not in redirect_targets):
        categories.add(ToolCallCategory.READS_SENSITIVE)

    if _sends_external(exe, args) or any(
        "/dev/tcp/" in t or "/dev/udp/" in t for t in segment
    ):
        categories.add(ToolCallCategory.SENDS_EXTERNAL)

    if _tampers_with_logs(exe, args, overwrite_targets):
        categories.add(ToolCallCategory.LOG_TAMPER)
    return categories


def _env_runs_command(args: list[str]) -> bool:
    """True when `env` wraps a command rather than printing the environment."""
    return any(not a.startswith("-") and "=" not in a for a in args)


def _is_sensitive_path(token: str) -> bool:
    """True for a token naming a credential or secret file."""
    candidates = [token]
    if "=" in token:
        candidates.append(token.split("=", 1)[1])
    for candidate in candidates:
        path = candidate.lstrip("@")
        path = re.sub(r"^(?:\$HOME|\$\{HOME\})", "~", path)
        if _SENSITIVE_PATH.search(path):
            return True
    return False


def _sends_external(exe: str, args: list[str]) -> bool:
    """True when the command transmits data to a remote host."""
    if exe == "curl":
        for i, arg in enumerate(args):
            if arg in _CURL_SEND_FLAGS or arg.startswith(("--data", "--form")):
                return True
            method = None
            if arg in ("-X", "--request") and i + 1 < len(args):
                method = args[i + 1]
            elif arg.startswith("-X") and len(arg) > 2:
                method = arg[2:]
            if method and method.upper() in _SEND_METHODS:
                return True
        return False
    if exe == "wget":
        return any(
            a.startswith(("--post-data", "--post-file", "--body-data", "--body-file"))
            or a.upper() in {f"--METHOD={m}" for m in _SEND_METHODS}
            for a in args
        )
    if exe in _RAW_SOCKET_TOOLS:
        return True
    if exe in _REMOTE_COPY_TOOLS:
        return any(_REMOTE_SPEC.match(a) and not a.startswith("/") for a in args)
    return False


def _tampers_with_logs(exe: str, args: list[str], overwrite_targets: set[str]) -> bool:
    """True when the command destroys, truncates or disables audit/shell history."""
    if any(_LOG_TARGET.search(t) for t in overwrite_targets):
        return True
    if any(_HISTORY_DISABLE.match(a) for a in [exe, *args]):
        return True
    log_args = [a for a in args if _LOG_TARGET.search(a)]
    if exe in _LOG_DESTROYERS and log_args:
        return True
    if exe == "history" and any(a in ("-c", "-w") for a in args):
        return True
    if exe == "unset" and any(a.startswith("HIST") for a in args):
        return True
    if exe == "ln" and "/dev/null" in args and log_args:
        return True
    if exe == "find" and log_args and ("-delete" in args or "rm" in args):
        return True
    return exe == "journalctl" and any(a.startswith("--vacuum") for a in args)


def _pipes_fetched_code_to_interpreter(
    segments: list[list[str]],
    connectors: list[str],
) -> bool:
    """True for `curl … | sh`, `base64 -d | sh` and `bash <(curl …)`."""
    for i, connector in enumerate(connectors):
        if connector in _PIPE_OPS:
            source, sink = segments[i], segments[i + 1]
        elif connector == "<(":
            source, sink = segments[i + 1], segments[i]
        else:
            continue
        if _executable(sink)[0] in _INTERPRETERS and _emits_fetched_code(source):
            return True
    return False


def _emits_fetched_code(segment: list[str]) -> bool:
    """True when a segment downloads or decodes a payload."""
    exe, args = _executable(segment)
    if exe in _DOWNLOADERS:
        return True
    if exe == "base64":
        return any(a in ("-d", "-D", "--decode") for a in args)
    if exe == "openssl":
        return "base64" in args and "-d" in args
    return exe == "xxd" and "-r" in args
