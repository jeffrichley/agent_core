"""Project-wide pytest configuration (loaded once at the repo root)."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, settings


def pytest_runtest_logstart(nodeid: str, location: object) -> None:
    """Windows-only breadcrumb naming each test as it starts.

    The intermittent windows-CI hang (agent_core#535) is a suspended coroutine
    on the ProactorEventLoop: ``pytest-timeout``'s ``timeout_method="thread"``
    dumps thread stacks but the hung test's own frame is *in the event-loop
    task*, not the main sync stack — so under ``-q`` the CI log never names the
    offending test. This breadcrumb (stderr, flushed, win32 only) means the
    LAST ``[run]`` line before a timeout dump identifies the culprit, without
    the noise of a full ``-v`` run on the Linux/macOS legs.
    """
    if sys.platform == "win32":
        print(f"[run] {nodeid}", file=sys.stderr, flush=True)

# Hypothesis enforces a per-example deadline (200ms by default) measured in
# wall-clock time. Under our default `-n auto` xdist run, many workers
# saturate every core, so a trivial example can blow that budget purely from
# CPU scheduling contention and raise DeadlineExceeded -- a test that passes
# in isolation fails flakily in the full parallel suite (observed on
# test_persistence_reader.py::...test_percentiles_are_sorted_by_rank).
#
# Our property tests exercise pure functions where wall-clock-per-example
# carries no signal, so disable the deadline (and the matching too_slow
# health check) globally. Correctness is still asserted by every test body.
settings.register_profile(
    "default",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("default")


# ---------------------------------------------------------------------------
# aiosqlite connection-leak guard
#
# Promoted from ``packages/core/tests/conftest.py`` to this repo-root conftest
# (2026-07-24) so it covers EVERY package's tests, not just core. The original
# core-only guard could not see leaks from tests in other packages (qa, voice,
# webcam, credentials, …) that build a bus / open a persistence connection —
# those leaked aiosqlite worker threads accumulated suite-wide until a random
# test hung on the Windows ProactorEventLoop (pytest-timeout → "ASGI callable
# returned without completing response"). Running the guard at repo scope turns
# that non-deterministic windows-CI hang into a localized, named failure at the
# offending test. See agent_core#535, #468 (original core-scoped guard).
# ---------------------------------------------------------------------------


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
    accumulate until the asyncio selector chokes and a random test hangs
    (``pytest-timeout`` → "ASGI callable returned without completing response")
    — a non-deterministic failure that blocks CI and wedges the merge queue.
    This guard turns that downstream flake into a deterministic, localized
    failure at the offending test.

    Fix a flagged test by building buses via the ``build_bus`` fixture, or by
    calling ``await store.close()`` / ``await bus.stop()`` in teardown.
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
