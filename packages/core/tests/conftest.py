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
from collections.abc import AsyncIterator, Awaitable, Callable
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


# NOTE: the ``fail_on_leaked_aiosqlite_connection`` autouse guard was promoted
# to the repo-root ``conftest.py`` (2026-07-24, agent_core#535) so it covers
# every package's tests, not just core. It still applies to these tests via
# inheritance from the root conftest.
