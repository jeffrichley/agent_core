"""Tests for daemon PID-file supervision utilities."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_core.daemon.supervisor import (
    is_alive,
    kill_tree,
    read_pid,
    remove_pid,
    write_pid,
)


def test_write_and_read_pid(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    write_pid(pid_file, 12345)
    assert pid_file.exists()
    assert read_pid(pid_file) == 12345


def test_read_pid_returns_none_when_missing(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    assert read_pid(pid_file) is None


def test_read_pid_returns_none_for_corrupt_file(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("not-a-number")
    assert read_pid(pid_file) is None


def test_remove_pid_is_idempotent(tmp_path: Path):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("123")
    remove_pid(pid_file)
    assert not pid_file.exists()
    remove_pid(pid_file)  # Should not raise.


def test_is_alive_false_for_nonexistent_pid():
    # Pick an unlikely-to-exist PID; psutil tolerates this.
    assert is_alive(999_999) is False


def test_is_alive_true_for_current_process():
    assert is_alive(os.getpid()) is True


def test_kill_tree_terminates_subprocess(tmp_path: Path):
    """Spawn a sleeping subprocess and verify kill_tree takes it down."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert is_alive(proc.pid) is True
        kill_tree(proc.pid)
        # Give the OS a moment to reap.
        for _ in range(20):
            if not is_alive(proc.pid):
                break
            time.sleep(0.1)
        assert is_alive(proc.pid) is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
