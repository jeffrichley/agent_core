"""A dead scheduler must become visible to the bus.

Regression guard for #586. APScheduler runs ``run_until_stopped`` inside its
own task group; when that task dies (a locked database, say) the group unwinds
internally, the library logs "Scheduler crashed", and nothing else happens.
``start()`` returned successfully long before, so the endpoint stays registered
and ``bus status`` keeps reporting it healthy — while every scheduled job has
stopped firing.

The machinery to handle this already existed and nothing fed it:
``BusHandle.spawn`` routes a failed tracked task to the bus, which calls
``EndpointSupervisor.record_failure``, which restarts with backoff and
quarantines after ``restarts_before_quarantine`` attempts. These tests cover
the link that was missing — the watchdog that notices and raises.
"""

from __future__ import annotations

import asyncio

import pytest
from apscheduler import RunState

from agent_core.endpoints import scheduler as scheduler_mod
from agent_core.endpoints.scheduler import SchedulerEndpoint


class _FakeScheduler:
    """Stands in for AsyncScheduler.

    The watchdog only reads ``state``, but ``stop()`` calls ``__aexit__`` on
    whatever is in ``_scheduler`` — so the fake must honour that too. It did
    not at first, and the resulting teardown AttributeError made the
    end-to-end test *fail without the fix for the wrong reason*, which would
    have made its negative control meaningless. A fake has to refuse and
    accept exactly what the real object does.
    """

    def __init__(self, state: RunState = RunState.started) -> None:
        self.state = state
        self.exited = False

    async def __aexit__(self, *exc_info: object) -> None:
        self.exited = True


def _endpoint(tmp_path) -> SchedulerEndpoint:
    return SchedulerEndpoint(name="scheduler", db_path=str(tmp_path / "s.db"))


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the watchdog's poll short so these tests stay quick."""
    monkeypatch.setattr(scheduler_mod, "_LIVENESS_POLL_SECONDS", 0.01)


@pytest.mark.asyncio
async def test_watchdog_raises_when_scheduler_died(tmp_path) -> None:
    """A stopped scheduler must raise, so spawn() reports it to the supervisor."""
    ep = _endpoint(tmp_path)
    ep._scheduler = _FakeScheduler(RunState.stopped)  # type: ignore[assignment]
    ep._stopping = False

    with pytest.raises(RuntimeError, match="no longer running"):
        await asyncio.wait_for(ep._watch_scheduler_liveness(), timeout=2.0)


@pytest.mark.asyncio
async def test_watchdog_stays_quiet_while_scheduler_runs(tmp_path) -> None:
    """A healthy scheduler must produce no failure report.

    Without this, a watchdog that raised unconditionally would still pass the
    crash test above while making every daemon restart-loop forever.
    """
    ep = _endpoint(tmp_path)
    ep._scheduler = _FakeScheduler(RunState.started)  # type: ignore[assignment]
    ep._stopping = False

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ep._watch_scheduler_liveness(), timeout=0.2)


@pytest.mark.asyncio
async def test_watchdog_exits_quietly_on_intentional_stop(tmp_path) -> None:
    """A clean shutdown must not be reported as a crash.

    stop() makes the scheduler not-running, which is indistinguishable from a
    crash by state alone. If the watchdog cried wolf here, every ordinary
    daemon shutdown would record an endpoint failure.
    """
    ep = _endpoint(tmp_path)
    ep._scheduler = _FakeScheduler(RunState.stopped)  # type: ignore[assignment]
    ep._stopping = True  # what stop() sets before tearing down

    await asyncio.wait_for(ep._watch_scheduler_liveness(), timeout=2.0)  # returns, no raise


@pytest.mark.asyncio
async def test_watchdog_exits_quietly_when_scheduler_already_gone(tmp_path) -> None:
    """stop() nulls _scheduler; the watchdog must treat that as shutdown."""
    ep = _endpoint(tmp_path)
    ep._scheduler = None
    ep._stopping = False

    await asyncio.wait_for(ep._watch_scheduler_liveness(), timeout=2.0)


@pytest.mark.asyncio
async def test_stop_sets_stopping_before_teardown(tmp_path) -> None:
    """The flag must be set by stop(), not merely exist.

    Ordering matters: stop() tears the scheduler down, so the flag has to be
    set first or the watchdog observes a not-running scheduler with
    _stopping still False and reports a spurious failure.
    """
    ep = _endpoint(tmp_path)
    assert ep._stopping is False
    await ep.stop()
    assert ep._stopping is True


# ---------------------------------------------------------------------------
# End-to-end wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_scheduler_reaches_the_supervisor(tmp_path, build_bus) -> None:
    """The whole point: a dead scheduler must become visible to the bus.

    The unit tests above prove the watchdog raises. This proves raising
    actually lands somewhere — spawn() -> task failure hook ->
    EndpointSupervisor.record_failure -> status 'restarting'. Without this the
    fix would rest on reading the wiring rather than exercising it, which is
    exactly how #586 went unnoticed: every individual piece was present.
    """
    import yaml

    from agent_core.bus.runner import build_bus_from_config  # noqa: F401  (fixture uses it)

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        yaml.dump(
            {
                "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
                "endpoints": [
                    {
                        "type": "builtin.scheduler",
                        "name": "scheduler",
                        "description": "scheduler under test",
                        "params": {"db_path": str(tmp_path / "sched.db")},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    bus, _ = await build_bus(cfg)
    await bus.start()
    try:
        supervisor = bus._supervisor
        assert supervisor is not None
        assert supervisor.state("scheduler").status == "active"

        # Simulate the crash: APScheduler's background task has died, so its
        # state is no longer `started`. The endpoint itself still looks fine.
        #
        # The real scheduler is kept and restored before teardown. Swapping it
        # out permanently orphans it — stop() would call __aexit__ on the fake
        # instead, leaving the real AsyncScheduler's resources unreleased. The
        # suite's leak guard caught exactly that.
        endpoint = bus._endpoints_by_name["scheduler"].endpoint
        real_scheduler = endpoint._scheduler
        endpoint._scheduler = _FakeScheduler(RunState.stopped)

        for _ in range(200):  # up to ~4s
            if supervisor.state("scheduler").status != "active":
                break
            await asyncio.sleep(0.02)

        state = supervisor.state("scheduler")
        assert state.status == "restarting", (
            "a dead scheduler must be reported to the supervisor; instead the "
            f"endpoint still reads {state.status!r} — the failure is invisible"
        )
        assert "no longer running" in (state.last_error or "")
        endpoint._scheduler = real_scheduler  # so stop() tears down the real one
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_stop_succeeds_after_the_watchdog_already_fired(tmp_path) -> None:
    """stop() must survive a watchdog that has already raised.

    Found while building this: awaiting a task that finished with an exception
    re-raises it, so cancel()+await in stop() blew up on exactly the endpoints
    the supervisor was about to restart — aborting before the engine was
    disposed and leaking an aiosqlite pool per restart. The supervisor calls
    stop() on every restart, so the fix for #586 would have reintroduced #468.
    """
    ep = _endpoint(tmp_path)
    ep._scheduler = _FakeScheduler(RunState.stopped)  # type: ignore[assignment]
    ep._stopping = False

    # Let the watchdog run to completion (it raises).
    task = asyncio.create_task(ep._watch_scheduler_liveness())
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
    ep._liveness_task = task
    assert task.done() and task.exception() is not None

    await ep.stop()  # must not raise

    assert ep._liveness_task is None
    assert ep._engine is None, "engine must still be disposed after a fired watchdog"
