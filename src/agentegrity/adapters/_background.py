"""One event loop on a daemon thread, where adapters run async layer checks.

Sync framework hooks (LangChain callbacks, the CrewAI hook, Agno tool hooks)
can be called while the host application's event loop is running, where
``asyncio.run`` fails. They hand the coroutine to this loop and block their
own thread until it finishes. Async hooks await the returned future instead.

The loop is shared by every adapter in the process because adapters made by
one :class:`~agentegrity.sdk.client.AgentegrityClient` share its layers. The
LLM layers keep a verdict cache, and running every check on one thread keeps
that cache single-threaded.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None


def _forget_parent_loop() -> None:
    """A forked child has the parent's loop object but not the thread running it."""
    global _loop, _thread
    _loop = None
    _thread = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_forget_parent_loop)


def _background_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="agentegrity-checks",
                                      daemon=True)
            thread.start()
            _loop, _thread = loop, thread
        return _loop


def submit(coro: Coroutine[Any, Any, _T]) -> concurrent.futures.Future[_T]:
    """Schedule ``coro`` on the background loop."""
    return asyncio.run_coroutine_threadsafe(coro, _background_loop())


def run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` on the background loop and block the calling thread until it returns."""
    if _thread is not None and threading.current_thread() is _thread:
        coro.close()
        raise RuntimeError("an async layer check cannot wait on its own event loop")
    return submit(coro).result()
