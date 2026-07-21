"""Shared fixtures for core bus tests.

The ``build_bus`` fixture exists to close a whole class of test-teardown
flakiness. ``build_bus_from_config`` opens an aiosqlite persistence connection
(a background thread). If a test builds a bus and never calls ``bus.stop()``,
that connection is finalized only when the object is garbage-collected — which
happens *after* the test's event loop has closed, raising
``RuntimeError: Event loop is closed`` during teardown. Individually these are
warnings, but under ``pytest-xdist --dist=loadscope`` a stray finalizer can
land on an unrelated test's teardown and escalate to a collected ERROR,
failing the whole run non-deterministically (agent_core#287). Building buses
through this factory guarantees every one is stopped — and its connection
closed — inside the test's own event loop.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.bus.core import Bus
from agent_core.bus.http_host import HTTPHost
from agent_core.bus.runner import build_bus_from_config

BusFactory = Callable[[Path], Awaitable[tuple[Bus, HTTPHost | None]]]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``looptime``-marked tests on Windows.

    ``looptime`` fakes the asyncio clock by replacing the event loop's
    ``select``. The Windows ``ProactorEventLoop`` (pytest-asyncio's default on
    win32) is IOCP-based rather than selector-based, so a ``looptime`` test
    wedges the loop and hangs until ``pytest-timeout`` aborts the whole run — a
    non-deterministic windows-latest failure. These tests exercise
    platform-independent ack timing and are fully covered on the Linux/macOS CI
    legs.
    """
    if sys.platform != "win32":
        return
    skip_win = pytest.mark.skip(
        reason="looptime is incompatible with the Windows ProactorEventLoop; "
        "covered on Linux/macOS CI"
    )
    for item in items:
        if item.get_closest_marker("looptime") is not None:
            item.add_marker(skip_win)


@pytest_asyncio.fixture
async def build_bus() -> AsyncIterator[BusFactory]:
    """Return a ``build_bus_from_config`` wrapper that tears down every bus.

    Use exactly like ``build_bus_from_config``::

        bus, http = await build_bus(config_path)

    Every bus (and its HTTP host, if any) produced by the returned factory is
    stopped on fixture teardown. ``Bus.stop`` / ``HTTPHost.stop`` are safe to
    call when never started, and ``Persistence.close`` is idempotent, so this
    coexists with tests that also stop their bus explicitly.
    """
    created: list[tuple[Bus, HTTPHost | None]] = []

    async def _factory(path: Path) -> tuple[Bus, HTTPHost | None]:
        bus, http = await build_bus_from_config(path)
        created.append((bus, http))
        return bus, http

    yield _factory

    for bus, http in created:
        if http is not None:
            await http.stop()
        await bus.stop()


def _live_aiosqlite_threads() -> list[threading.Thread]:
    """Return the currently-alive ``aiosqlite`` connection worker threads.

    ``aiosqlite.Connection`` runs one ``threading.Thread`` per open connection
    (``target=_connection_worker_thread``) until the connection is closed. The
    thread is matched by its target function name — robust across aiosqlite
    versions — with the auto-generated thread name as a fallback.
    """
    threads: list[threading.Thread] = []
    for t in threading.enumerate():
        if not t.is_alive():
            continue
        target = getattr(t, "_target", None)
        target_name = getattr(target, "__name__", "") if target is not None else ""
        if target_name == "_connection_worker_thread" or ("_connection_worker_thread" in t.name):
            threads.append(t)
    return threads


@pytest.fixture(autouse=True)
def fail_on_leaked_aiosqlite_connection() -> Iterator[None]:
    """Fail any test that leaks an ``aiosqlite`` connection thread.

    Each ``aiosqlite.connect`` starts a background ``Connection`` thread that
    lives until the connection is closed. A test that opens a bus/persistence
    connection and never closes it leaks that thread; across the suite they
    accumulate into the thousands until the asyncio selector chokes and a
    random test hangs (``pytest-timeout`` → "ASGI callable returned without
    completing response") — a non-deterministic failure that blocks CI and
    wedges the merge queue. This guard turns that downstream flake into a
    deterministic, localized failure at the offending test.

    Fix a flagged test by building buses via the :func:`build_bus` fixture, or
    by calling ``await store.close()`` / ``await bus.stop()`` in teardown.
    """
    before = {id(t) for t in _live_aiosqlite_threads()}
    yield
    # aiosqlite joins its worker thread asynchronously after ``close()``; allow
    # a brief grace so a properly-closed connection mid-teardown is not flagged.
    leaked: list[threading.Thread] = []
    for _ in range(100):  # up to ~1s
        leaked = [t for t in _live_aiosqlite_threads() if id(t) not in before]
        if not leaked:
            break
        time.sleep(0.01)
    if leaked:
        pytest.fail(
            f"leaked {len(leaked)} aiosqlite connection thread(s) — the test "
            "opened a bus/persistence connection without closing it. Build "
            "buses via the `build_bus` fixture, or `await store.close()` / "
            "`await bus.stop()` in teardown.",
            pytrace=False,
        )
