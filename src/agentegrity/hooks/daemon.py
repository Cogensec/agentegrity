"""Per-session hook daemon and the fail-open hook client.

Hosts run a fresh process for every hook, so state lives in one daemon
per (host, session_id), reached over a Unix socket. The client
(``agentegrity hook --host …``) starts the daemon on first use under a
file lock, forwards each payload and prints the host output.

Failure policy:

* The daemon cannot start or sockets are unavailable (e.g. Windows): the
  client evaluates the call in-process from the persisted chain. Single
  call rules still enforce; cross-call state and streaming are lost.
* The daemon is up but slow: the client stays silent (fail-open) rather
  than racing it for the chain file.
* Anything else unexpected: stay silent, and the host's own permission
  flow decides. A hook must never break the session.

The daemon exits on ``SessionEnd`` or after ``AGENTEGRITY_HOOK_IDLE_SECONDS``
(default 1800) without a hook, since hosts give ``SessionEnd`` very little
time and a crash sends nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows: no daemon, calls are evaluated in-process
    fcntl = None  # type: ignore[assignment]

from agentegrity.core.profile import RiskTier
from agentegrity.hooks.protocol import HOSTS, parse_payload
from agentegrity.hooks.session import HookSession

_DEFAULT_IDLE_SECONDS = 1800
_DEFAULT_REPLY_TIMEOUT = 10.0
_SPAWN_WAIT_SECONDS = 5.0
_MAX_MESSAGE = 8 * 1024 * 1024


class DaemonUnavailable(Exception):
    """The per-session daemon cannot run here; evaluate in-process instead."""


def run_hook(host: str, raw: str, env: Mapping[str, str]) -> str:
    """Handle one hook invocation; return what to print on stdout (often nothing)."""
    try:
        if host not in HOSTS or env.get("AGENTEGRITY_HOOK_DISABLED") == "1":
            return ""
        payload = parse_payload(raw)
        if payload is None:
            return ""
        try:
            output = _via_daemon(host, payload, env)
        except DaemonUnavailable:
            output = _in_process(host, payload, env)
        return json.dumps(output) if output else ""
    except Exception:  # noqa: BLE001 - the hook must never break a session
        return ""


def serve(host: str, session_id: str, env: Mapping[str, str]) -> int:
    """Run the daemon for one session until SessionEnd or the idle timeout."""
    path = socket_path(host, session_id, env)
    if _alive(path):
        return 0
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    session = _new_session(host, session_id, env, stream=True)
    idle = float(env.get("AGENTEGRITY_HOOK_IDLE_SECONDS", _DEFAULT_IDLE_SECONDS))
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    try:
        server.bind(str(path))
        bound = True
        os.chmod(path, 0o600)
        server.listen(16)
        server.settimeout(1.0)
        last_seen = time.monotonic()
        while not session.ended:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                if time.monotonic() - last_seen > idle:
                    session.close()
                continue
            last_seen = time.monotonic()
            with conn:
                _answer(conn, session)
    finally:
        server.close()
        if bound:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
    return 0


def state_dir(host: str, env: Mapping[str, str]) -> Path:
    """Where a host's decision chains live (``~/.agentegrity/<host>`` by default)."""
    base = env.get("AGENTEGRITY_HOOK_DIR") or os.path.join(
        os.path.expanduser("~"), ".agentegrity"
    )
    return Path(base) / host


def chain_path(host: str, session_id: str, env: Mapping[str, str]) -> Path:
    """The persisted decision chain for one session, for ``agentegrity verify-decisions``."""
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:128]
    return state_dir(host, env) / f"{safe_id}.chain.json"


def socket_path(host: str, session_id: str, env: Mapping[str, str]) -> Path:
    """The session's socket in a private runtime directory (short path for AF_UNIX)."""
    base = env.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    directory = Path(base) / f"agentegrity-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.stat()
    if info.st_uid != os.getuid() or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise DaemonUnavailable(f"{directory} is not private to this user")
    digest = hashlib.sha256(f"{host}\0{session_id}".encode()).hexdigest()[:24]
    return directory / f"{digest}.sock"


def _via_daemon(host: str, payload: Mapping[str, Any], env: Mapping[str, str]) -> Any:
    """Forward a payload to the session daemon, starting it if needed."""
    if fcntl is None or not hasattr(socket, "AF_UNIX"):
        raise DaemonUnavailable("Unix sockets are not available")
    session_id = payload["session_id"]
    path = socket_path(host, session_id, env)
    if payload.get("hook_event_name") == "SessionEnd" and not _alive(path):
        return None
    with _locked(path.with_suffix(".lock")):
        if not _alive(path):
            _spawn(host, session_id, path)
    timeout = float(env.get("AGENTEGRITY_HOOK_TIMEOUT", _DEFAULT_REPLY_TIMEOUT))
    return _request(path, payload, timeout)


def _in_process(host: str, payload: Mapping[str, Any], env: Mapping[str, str]) -> Any:
    """Evaluate one call without a daemon: chain resumes from disk, nothing streams."""
    session = _new_session(host, payload["session_id"], env, stream=False)
    return session.handle(payload)


def _new_session(
    host: str,
    session_id: str,
    env: Mapping[str, str],
    *,
    stream: bool,
) -> HookSession:
    """Build a session configured from the hook environment."""
    try:
        risk_tier = RiskTier(env.get("AGENTEGRITY_RISK_TIER", "high"))
    except ValueError:
        risk_tier = RiskTier.HIGH
    mode = "alert" if env.get("AGENTEGRITY_HOOK_MODE") == "alert" else "enforce"
    return HookSession(
        host, session_id, chain_path(host, session_id, env),
        mode=mode, risk_tier=risk_tier, stream=stream,
        agent_id=env.get("AGENTEGRITY_AGENT_ID"),
        agent_name=env.get("AGENTEGRITY_AGENT_NAME"),
        model_id=env.get("AGENTEGRITY_MODEL_ID"),
    )


def _spawn(host: str, session_id: str, path: Path) -> None:
    """Start the daemon detached and wait for its socket."""
    try:
        subprocess.Popen(
            [
                sys.executable, "-m", "agentegrity",
                "hook-daemon", "--host", host, "--session", session_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise DaemonUnavailable(f"cannot start daemon: {exc}") from exc
    deadline = time.monotonic() + _SPAWN_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _alive(path):
            return
        time.sleep(0.05)
    raise DaemonUnavailable("daemon did not come up")


def _request(path: Path, payload: Mapping[str, Any], timeout: float) -> Any:
    """Send one payload and return the daemon's output; None on timeout (fail-open)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(str(path))
        except OSError as exc:
            raise DaemonUnavailable(f"cannot connect: {exc}") from exc
        try:
            client.sendall(json.dumps(payload).encode() + b"\n")
            reply = _read_line(client)
        except socket.timeout:
            return None
    return json.loads(reply).get("output") if reply else None


def _answer(conn: socket.socket, session: HookSession) -> None:
    """Serve one request on an accepted connection."""
    conn.settimeout(10.0)
    try:
        raw = _read_line(conn)
        payload = parse_payload(raw.decode()) if raw else None
        output = session.handle(payload) if payload is not None else None
    except Exception:  # noqa: BLE001 - one bad request must not kill the session
        output = None
    with contextlib.suppress(OSError):
        conn.sendall(json.dumps({"output": output}).encode() + b"\n")


def _read_line(conn: socket.socket) -> bytes:
    """Read one newline-terminated message, bounded in size."""
    buffer = b""
    while not buffer.endswith(b"\n") and len(buffer) < _MAX_MESSAGE:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buffer += chunk
    return buffer.strip()


def _alive(path: Path) -> bool:
    """True when a daemon accepts connections on ``path``."""
    if not path.exists():
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        try:
            probe.connect(str(path))
            return True
        except OSError:
            return False


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an exclusive file lock so one client starts the daemon."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
