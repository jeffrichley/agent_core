"""Keep the presence watcher running, and leave evidence that it did.

Why this exists: on 2026-08-14 the watcher stopped after eight caught transient
failures and nothing brought it back. It stayed down 56 hours. **The cause of
the stop was never established** — the log ends on a complete line, which is
consistent with an external kill, a normal exit, a closed parent shell, or a
reboot, and distinguishes none of them.

That unknown is the whole argument for supervising rather than for adding more
error handling. The watcher's own ``except Exception`` already worked; it caught
every one of those eight failures. A supervisor restores a stopped watcher
*whatever stopped it*, which is precisely the property you want when you cannot
name the cause.

**Do not read a working supervisor as evidence the cause was diagnosed.** If the
real cause also defeats a supervisor, the only thing that will reveal it is the
restart record this module writes.

Observability is not a nicety here. A watcher that dies and is revived every
forty minutes is indistinguishable from one that never fell over, unless the
revivals are counted somewhere a reader already looks — so this writes
``supervisor.json`` beside the state file, and the injector renders the count
into the line it emits every turn.

The regress terminates at the OS: this process is started by the platform's
service manager (Windows Task Scheduler / launchd / systemd), which is watched
by the operating system and needs nothing from us. Same base case as a
timestamped file — the thing at the bottom must need no watcher of its own.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

#: Restart backoff, in seconds, indexed by consecutive-failure count. A watcher
#: that cannot start at all must not be respawned in a hot loop — that turns one
#: broken camera into a pegged core and a log nobody can read.
BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 5.0, 15.0, 30.0, 60.0)

#: Restarts older than this stop counting toward ``restarts_recent``. Without a
#: window the count only ever grows, and "312 restarts" since some forgotten
#: date says nothing about whether anything is wrong *now*.
RESTART_WINDOW_SECONDS = 3600.0


def supervisor_path_for(state_path: Path) -> Path:
    """Return the supervisor-record path that pairs with ``state_path``."""
    return state_path.with_name("supervisor.json")


def write_supervisor_record(
    path: Path,
    *,
    beat_at: float,
    restarts_recent: int,
    last_restart_at: float | None,
    child_pid: int | None,
    last_exit_code: int | None,
) -> None:
    """Atomically write the supervisor's own liveness + restart record.

    Mirrors the watcher's heartbeat discipline one level up: the supervisor
    must prove *it* is turning, or it becomes the new silent single point of
    failure — the bug moved one layer out rather than fixed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "beat_at": beat_at,
        "restarts_recent": restarts_recent,
        "last_restart_at": last_restart_at,
        "child_pid": child_pid,
        "last_exit_code": last_exit_code,
        "pid": os.getpid(),
    }
    tmp = path.with_name(path.name + ".sup.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _backoff_for(consecutive: int) -> float:
    """Return the backoff delay for the nth consecutive failed start."""
    idx = min(consecutive, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[idx]


def run_supervised(
    *,
    child_argv: Sequence[str],
    state_path: Path,
    max_restarts: int | None = None,
    spawn: Callable[[Sequence[str]], subprocess.Popen[bytes]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> int:
    """Run ``child_argv`` forever, restarting it whenever it exits.

    Every exit is a restart, deliberately including exit code 0. A watcher that
    is supposed to run until the machine stops has no successful termination —
    treating a clean exit as "it meant to do that" is exactly how the 56-hour
    outage would recur silently, since we never established that the 08-14 stop
    was *unclean*.

    ``max_restarts`` bounds the loop for tests; ``None`` means run forever.
    ``spawn`` is an injectable seam so the loop is testable without processes.

    Returns the last child exit code (or 0 if it never ran).
    """
    spawn_fn = spawn or (lambda argv: subprocess.Popen(list(argv)))
    sup_path = supervisor_path_for(state_path)
    restart_times: list[float] = []
    consecutive_fast_exits = 0
    last_exit: int | None = None
    last_restart_at: float | None = None
    restarts = 0

    while max_restarts is None or restarts <= max_restarts:
        started = clock()
        try:
            proc = spawn_fn(child_argv)
        except Exception:
            log.exception("failed to spawn presence watcher; backing off")
            consecutive_fast_exits += 1
            write_supervisor_record(
                sup_path,
                beat_at=clock(),
                restarts_recent=len(restart_times),
                last_restart_at=last_restart_at,
                child_pid=None,
                last_exit_code=last_exit,
            )
            sleep_fn(_backoff_for(consecutive_fast_exits))
            restarts += 1
            continue

        write_supervisor_record(
            sup_path,
            beat_at=clock(),
            restarts_recent=len(restart_times),
            last_restart_at=last_restart_at,
            child_pid=proc.pid,
            last_exit_code=last_exit,
        )
        last_exit = proc.wait()
        ran_for = clock() - started

        # A child that exits almost immediately is failing to start, not
        # crashing mid-run; those get the backoff. One that ran a while and then
        # stopped is the 08-14 shape and should come straight back.
        consecutive_fast_exits = consecutive_fast_exits + 1 if ran_for < 10.0 else 0

        now = clock()
        restart_times.append(now)
        restart_times[:] = [t for t in restart_times if now - t <= RESTART_WINDOW_SECONDS]
        last_restart_at = now
        restarts += 1

        log.warning(
            "presence watcher exited (code=%s, ran %.1fs); restarting (%d in the last hour)",
            last_exit,
            ran_for,
            len(restart_times),
        )
        write_supervisor_record(
            sup_path,
            beat_at=now,
            restarts_recent=len(restart_times),
            last_restart_at=last_restart_at,
            child_pid=None,
            last_exit_code=last_exit,
        )
        if max_restarts is not None and restarts > max_restarts:
            break
        sleep_fn(_backoff_for(consecutive_fast_exits))

    return last_exit or 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: ``python -m agent_core_webcam.presence.supervisor -- <watcher argv>``."""
    args = list(argv if argv is not None else sys.argv[1:])
    if "--" in args:
        idx = args.index("--")
        state_path = Path(args[0]) if idx > 0 else _default_state_path()
        child = args[idx + 1 :]
    else:  # no explicit split: supervise the default watcher invocation
        state_path = _default_state_path()
        child = [sys.executable, "-m", "agent_core_webcam.presence.cli", "watch"]
    if not child:
        print("usage: supervisor [state.json] -- <watcher argv>", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run_supervised(child_argv=child, state_path=state_path)


def _default_state_path() -> Path:
    return Path.home() / ".agent-core" / "presence" / "state.json"


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())
