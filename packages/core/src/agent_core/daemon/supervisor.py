"""PID file management and cross-platform process tree kill.

Modeled on Pepper's process.py but lives in agent_core. Used by
`agent-core daemon start/stop/status` to supervise the long-running
`agent-core bus run` subprocess.
"""

from __future__ import annotations

from pathlib import Path

import psutil


def write_pid(pid_file: Path, pid: int) -> None:
    """Write a PID to the PID file."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid), encoding="utf-8")


def read_pid(pid_file: Path) -> int | None:
    """Read a PID from the PID file. None if missing or corrupt."""
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def remove_pid(pid_file: Path) -> None:
    """Remove the PID file if it exists. Idempotent."""
    pid_file.unlink(missing_ok=True)


def is_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently running."""
    return bool(psutil.pid_exists(pid))


def kill_tree(pid: int) -> None:
    """Kill a process and all its descendants. Tolerates already-dead processes."""
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                # Justified: the child died between enumeration and kill —
                # already gone, nothing to do.
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            # Justified: the parent died between enumeration and kill —
            # already gone, nothing to do.
            pass
        psutil.wait_procs([*children, parent], timeout=5)
    except psutil.NoSuchProcess:
        return
