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
instead. Comments are dropped and heredoc bodies are read as data unless
a shell executes them; when the quoting does not balance, nothing is
dropped. This is a floor for plainly-written commands, not a sandbox:
anything the shell computes at runtime is outside its reach.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from enum import Enum
from typing import Any, NamedTuple


class ToolCallCategory(str, Enum):
    """Behavioral category of a single tool call."""

    READS_SENSITIVE = "reads_sensitive"
    SENDS_EXTERNAL = "sends_external"
    REMOTE_CODE_EXEC = "remote_code_exec"
    LOG_TAMPER = "log_tamper"
    OBFUSCATED_COMMAND = "obfuscated_command"
    # An interpreter given a program inline (`python -c`, `node -e`, `eval`,
    # stdin, a heredoc): code the agent wrote, as opposed to a script on disk.
    # `bash -c 'cmd'` is not tagged; its command string is classified as shell.
    RUNS_INLINE_CODE = "runs_inline_code"


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
# apply_patch envelope (Codex): file edits, not shell.
PATCH_MARKER = "*** Begin Patch"
_PATCH_FILE_OP = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$", re.MULTILINE)

_SEGMENT_OPS = {"|", "|&", "||", "&&", ";", "&", "(", ")", "<("}
_PIPE_OPS = {"|", "|&"}
_WRAPPERS = {"sudo", "doas", "nohup", "time", "command", "exec", "nice"}
_DOWNLOADERS = {"curl", "wget", "fetch"}
_INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish",
    "python", "python2", "python3", "perl", "ruby", "node", "php", "pwsh",
}
# Short-flag letters that make an interpreter run code given on the command
# line. They count anywhere in a cluster: `python -Ic`, `perl -ne`.
_INLINE_CODE_LETTERS = {
    "python": "c",
    "node": "ep",
    "perl": "eE",
    "ruby": "e",
    "php": "r",
}
_INLINE_CODE_LONG = {"node": {"--eval", "--print"}}
# Short options whose value is the next argument (or the rest of the cluster).
_VALUE_LETTERS = {"python": "WX", "node": "r", "perl": "IM", "ruby": "Ir"}
# Short options after which the next argument is not code: `python -m pytest`.
_STOP_LETTERS = {"python": "m"}
# PowerShell's -Command is its `bash -c`; only an encoded command is opaque.
_PWSH_ENCODED = {"-encodedcommand", "-ec", "-e", "-enc"}
_POSIX_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
# Shell options that take the next argument as a value: `bash -o pipefail -c`.
_SHELL_VALUE_OPTIONS = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_HEREDOC_TOKENS = {"<<", "<<-", "<<<"}
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
# `<<EOF`, `<<-'EOF'`, `<< "E"OF`; `<<<` (here-string) is matched first and skipped.
_HEREDOC_OP = re.compile(r"<<(-?)[ \t]*((?:\\.|'[^'\n]*'|\"[^\"\n]*\"|[^\s;&|<>()'\"\\])+)")
_HEREDOC_QUOTING = re.compile(r"['\"\\]")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "source"}
# A line ending in a pipe or list operator continues after the heredoc body.
_CONTINUED_LINE = re.compile(r"(?:\|\|?|\|&|&&)\s*$")
_WORD_BREAKS = " \t\n;|&()"
# Nested contexts, longest opener first: arithmetic, substitution, parameter
# expansion, ANSI-C and plain quotes. Inside "..." only expansions nest.
_OPENERS = (
    ("$((", "(("), ("((", "(("), ("$(", "("), ("${", "{"), ("$'", "$'"),
    ("(", "("), ("'", "'"), ('"', '"'), ("`", "`"),
)
_QUOTED_OPENERS = (("$((", "(("), ("$(", "("), ("${", "{"), ("`", "`"))
_CLOSERS = {"((": "))", "(": ")", "{": "}", "'": "'", "$'": "'", '"': '"', "`": "`"}
# Where `#` starts a comment and `<<` opens a heredoc.
_COMMAND_CONTEXTS = {"", "(", "`"}


class _Heredoc(NamedTuple):
    delimiter: str
    quoted: bool
    strip_tabs: bool


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
            # `commands` is a list of commands; any other list is one argv.
            parts = [str(v) for v in value]
            value = "\n".join(parts) if key == "commands" else shlex.join(parts)
        if not isinstance(value, str) or not value.strip():
            continue
        if PATCH_MARKER in value:
            categories |= _classify_patch(value)
        else:
            categories |= _classify_shell(value)
    if not _WRITE_TOOL.search(tool_name or ""):
        for key in _PATH_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and _is_sensitive_path(value):
                categories.add(ToolCallCategory.READS_SENSITIVE)
    return frozenset(categories)


def _classify_patch(patch: str) -> set[ToolCallCategory]:
    """Classify a patch envelope by the files it touches; its body is data, not shell."""
    for operation, path in _PATCH_FILE_OP.findall(patch):
        if operation == "Delete" and _LOG_TARGET.search(path.strip()):
            return {ToolCallCategory.LOG_TAMPER}
    return set()


def _classify_shell(command: str) -> set[ToolCallCategory]:
    """Classify one shell command string."""
    script, executed_bodies = _split_heredocs(command)
    categories: set[ToolCallCategory] = set()
    for body in executed_bodies:
        categories |= _classify_shell(body)
    tokens = _tokenize(script)
    segments, connectors = _split_segments(tokens)
    for segment in segments:
        categories |= _classify_segment(segment)
    if _pipes_fetched_code_to_interpreter(segments, connectors):
        categories.add(ToolCallCategory.REMOTE_CODE_EXEC)
    if _pipes_code_to_interpreter(segments, connectors):
        categories.add(ToolCallCategory.RUNS_INLINE_CODE)
    if _SUBSTITUTED_FETCH.search(" ".join(tokens)):
        categories.add(ToolCallCategory.REMOTE_CODE_EXEC)
    return categories


def _split_heredocs(command: str) -> tuple[str, list[str]]:
    """Drop comments and heredoc bodies; return the script and the bodies that run as code.

    A body is code when the line that opens it feeds a shell. Otherwise it is
    data, and only the command substitutions of an unquoted body run. When the
    quoting does not balance, the command comes back whole so nothing is hidden.
    """
    kept: list[str] = []
    size = line_start = position = 0
    executed: list[str] = []
    pending: list[_Heredoc] = []
    stack: list[str] = []
    while position < len(command):
        char = command[position]
        top = stack[-1] if stack else ""
        in_command = top in _COMMAND_CONTEXTS
        step = char
        if top in ("'", "$'"):
            if top == "$'" and char == "\\":
                step = command[position:position + 2]
            elif char == "'":
                stack.pop()
        elif char == "\\":
            step = command[position:position + 2]
        elif top and command.startswith(_CLOSERS[top], position):
            step = _CLOSERS[top]
            stack.pop()
        elif in_command and char == "#" and (not kept or kept[-1][-1] in _WORD_BREAKS):
            end = command.find("\n", position)
            position = len(command) if end < 0 else end
            continue
        elif in_command and command.startswith("<<<", position):
            step = "<<<"
        elif in_command and (heredoc := _HEREDOC_OP.match(command, position)):
            word = heredoc.group(2)
            pending.append(_Heredoc(
                delimiter=_HEREDOC_QUOTING.sub("", word),
                quoted=bool(_HEREDOC_QUOTING.search(word)),
                strip_tabs=heredoc.group(1) == "-",
            ))
            step = heredoc.group(0)
        elif in_command and char == "\n" and pending:
            runs = _feeds_a_shell("".join(kept)[line_start:])
            position += 1
            for pending_heredoc in pending:
                body, position = _read_heredoc_body(command, position, pending_heredoc)
                if runs:
                    executed.append(body)
                elif not pending_heredoc.quoted:
                    executed.extend(_substitutions(body))
            pending.clear()
            kept.append("\n")
            size = line_start = size + 1
            continue
        elif opener := _opener(command, position, top):
            step, context = opener
            stack.append(context)
        elif in_command and char == "\n":
            line_start = size + 1
        kept.append(step)
        size += len(step)
        position += len(step)
    if stack:
        return command, []
    return "".join(kept), executed


def _opener(command: str, position: int, top: str) -> tuple[str, str] | None:
    """Return (opening text, context it pushes) if a nested context starts here."""
    for text, context in _QUOTED_OPENERS if top == '"' else _OPENERS:
        if command.startswith(text, position):
            return text, context
    return None


def _feeds_a_shell(line: str) -> bool:
    """True when a heredoc opened on this logical line may be run by a shell."""
    if _CONTINUED_LINE.search(line):
        return True
    segments, _ = _split_segments(_tokenize(line))
    return any(
        segment and (segment[0] == "." or any(os.path.basename(t) in _SHELLS for t in segment))
        for segment in segments
    )


def _read_heredoc_body(command: str, start: int, heredoc: _Heredoc) -> tuple[str, int]:
    """Return the body starting at `start` and the position after its delimiter line."""
    lines: list[str] = []
    position = start
    while position < len(command):
        end = command.find("\n", position)
        end = len(command) if end < 0 else end
        line = command[position:end]
        position = end + 1
        if (line.lstrip("\t") if heredoc.strip_tabs else line) == heredoc.delimiter:
            break
        lines.append(line)
    return "\n".join(lines), min(position, len(command))


def _substitutions(body: str) -> list[str]:
    """Return the `$(...)` and backtick commands an unquoted heredoc body expands."""
    found: list[str] = []
    position = 0
    while position < len(body):
        if body[position] == "\\":
            position += 2
        elif body.startswith("$(", position):
            depth, end = 1, position + 2
            while end < len(body) and depth:
                depth += {"(": 1, ")": -1}.get(body[end], 0)
                end += 1
            found.append(body[position + 2:end - 1 if depth == 0 else end])
            position = end
        elif body[position] == "`":
            end = body.find("`", position + 1)
            end = len(body) if end < 0 else end
            found.append(body[position + 1:end])
            position = end + 1
        else:
            position += 1
    return found


def _tokenize(command: str) -> list[str]:
    """Split like a POSIX shell (quotes collapsed, operators separated)."""
    prepared = command.replace("\\\n", "").replace("\n", " ; ")
    lexer = shlex.shlex(prepared, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # Comments are already dropped; shlex would also end a word at `#`.
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        # Unbalanced quotes: the shell would reject it too, but still
        # classify what is there rather than going blind.
        return prepared.split()


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
        elif os.path.basename(token) in _WRAPPERS or (
            os.path.basename(token) == "env" and _env_runs_command(segment[index + 1:])
        ):
            index += 1
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
        elif os.path.basename(token) == "timeout":
            index += 1
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
            index += 1  # the duration
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
    if _runs_inline_code(exe, args):
        categories.add(ToolCallCategory.RUNS_INLINE_CODE)
    script = _shell_command_string(exe, args)
    if script is not None:
        categories |= _classify_shell(script)
    return categories


def _shell_command_string(exe: str, args: list[str]) -> str | None:
    """The command string of `bash -c '…'` (or `-lc`, `-o pipefail -c`), which runs as shell."""
    if exe not in _POSIX_SHELLS:
        return None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--command="):
            return arg.split("=", 1)[1]
        if arg in _SHELL_VALUE_OPTIONS:
            index += 2
            continue
        if not arg.startswith(("-", "+")):
            return None
        if arg == "--command" or (not arg.startswith("--") and "c" in arg[1:]):
            return args[index + 1] if index + 1 < len(args) else None
        index += 1
    return None


def _interpreter(exe: str) -> str:
    """The interpreter family of an executable (`python3.12`, `pwsh.exe`), or ''."""
    exe = exe.lower().removesuffix(".exe")
    if re.fullmatch(r"python[\d.]*", exe):
        return "python"
    if exe in ("powershell", "pwsh"):
        return "pwsh"
    if exe in _INLINE_CODE_LETTERS or exe in _POSIX_SHELLS:
        return exe
    return ""


def _runs_inline_code(exe: str, args: list[str]) -> bool:
    """True when an interpreter is given its program inline: a code flag, `eval`, or a heredoc."""
    if exe == "eval":
        return bool(args)
    kind = _interpreter(exe)
    if not kind or kind in _POSIX_SHELLS:
        # A shell's -c string and heredoc are shell, classified as such.
        return False
    if kind == "pwsh":
        return any(a.lower() in _PWSH_ENCODED for a in args)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _HEREDOC_TOKENS:
            return True
        if arg == "-":
            # The program comes from stdin: a heredoc or a pipe (see below).
            return any(a in _HEREDOC_TOKENS for a in args[index + 1:])
        if not arg.startswith("-"):
            # A script on disk; what follows is its arguments and stdin.
            return False
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in _INLINE_CODE_LONG.get(kind, ()):
                return True
        else:
            for position, letter in enumerate(arg[1:]):
                if letter in _INLINE_CODE_LETTERS[kind]:
                    return True
                if letter in _STOP_LETTERS.get(kind, ""):
                    return False
                if letter in _VALUE_LETTERS.get(kind, ""):
                    if position == len(arg) - 2:
                        index += 1  # the value is the next argument
                    break
        index += 1
    return False


def _pipes_code_to_interpreter(
    segments: list[list[str]],
    connectors: list[str],
) -> bool:
    """True for `… | python` and `… | bash`: an interpreter reading its program from stdin."""
    for i, connector in enumerate(connectors):
        if connector not in _PIPE_OPS:
            continue
        exe, args = _executable(segments[i + 1])
        if _interpreter(exe) and all(a.startswith("-") for a in args):
            return True
    return False


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
