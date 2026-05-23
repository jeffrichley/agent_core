"""Phase 3.5 coexistence test: prod / source / test all run simultaneously
without conflict.

Uses pytest.mark.slow, matching the existing windows-only integration job
convention (see `test_prod_and_dev_daemons_coexist` in test_daemon_cli.py).

Invocation pattern: Typer CliRunner (in-process), AGENT_CORE_HOME escape hatch
to give each instance a disjoint tmp home — same approach as the existing
two-instance test.  Ports are set well away from defaults (8789/8788/8787) to
avoid colliding with any real daemon already running on the machine.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml as _yaml
from typer.testing import CliRunner

from agent_core.daemon.cli import app as daemon_app
from agent_core.daemon.supervisor import is_alive, read_pid


runner = CliRunner()

pytestmark = pytest.mark.slow


def test_three_instances_run_simultaneously_without_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spin up prod / source / test daemons against tmp homes + non-default
    ports; verify all three reach ready state and have disjoint PIDs; verify
    stopping one leaves the other two alive; tear down cleanly.

    Proves the core Phase 3.5 property: three instances, three homes, three
    ports, fully independent lifecycle — no shared lock file, no port clash,
    no cross-home PID write.
    """
    prod_home = tmp_path / "prod"
    source_home = tmp_path / "source"
    test_home = tmp_path / "test"

    # Use non-default ports well away from 8789/8788/8787 to avoid colliding
    # with any real daemon already running on the machine.
    ports = {
        "prod": 18789,
        "source": 18788,
        "test": 18787,
    }

    def _run(args: list[str], home: Path):
        monkeypatch.setenv("AGENT_CORE_HOME", str(home))
        return runner.invoke(daemon_app, args)

    # Scaffold minimal configs via `daemon init`.
    assert _run(["init"], prod_home).exit_code == 0
    assert _run(["init", "--instance", "source"], source_home).exit_code == 0
    assert _run(["init", "--instance", "test"], test_home).exit_code == 0

    # Rewrite each config to a free, distinct port.
    for home, port in (
        (prod_home, ports["prod"]),
        (source_home, ports["source"]),
        (test_home, ports["test"]),
    ):
        cfg = home / "agent_core.yaml"
        data = _yaml.safe_load(cfg.read_text())
        data["http"]["bind_port"] = port
        cfg.write_text(_yaml.safe_dump(data), encoding="utf-8")

    try:
        # Start all three daemons.
        assert _run(["start"], prod_home).exit_code == 0
        assert _run(["start"], source_home).exit_code == 0
        assert _run(["start"], test_home).exit_code == 0

        # Wait up to 4 s for all three to come up.
        for _ in range(40):
            prod_pid = read_pid(prod_home / "daemon.pid")
            source_pid = read_pid(source_home / "daemon.pid")
            test_pid = read_pid(test_home / "daemon.pid")
            if (
                prod_pid
                and source_pid
                and test_pid
                and is_alive(prod_pid)
                and is_alive(source_pid)
                and is_alive(test_pid)
            ):
                break
            time.sleep(0.1)

        # All three report "is running".
        assert "is running" in _run(["status"], prod_home).stdout
        assert "is running" in _run(["status"], source_home).stdout
        assert "is running" in _run(["status"], test_home).stdout

        # PIDs are unique — no collision.
        prod_pid = read_pid(prod_home / "daemon.pid")
        source_pid = read_pid(source_home / "daemon.pid")
        test_pid = read_pid(test_home / "daemon.pid")
        assert prod_pid is not None
        assert source_pid is not None
        assert test_pid is not None
        all_pids = {prod_pid, source_pid, test_pid}
        assert len(all_pids) == 3, (
            f"PIDs collided — expected three distinct PIDs, got: "
            f"prod={prod_pid}, source={source_pid}, test={test_pid}"
        )

        # Each PID file lives in the correct home — no cross-home write.
        assert (prod_home / "daemon.pid").exists()
        assert (source_home / "daemon.pid").exists()
        assert (test_home / "daemon.pid").exists()

        # Stop source — prod and test must still be alive.
        assert _run(["stop"], source_home).exit_code == 0
        assert "is running" in _run(["status"], prod_home).stdout
        assert "is running" in _run(["status"], test_home).stdout
        assert "not running" in _run(["status"], source_home).stdout

    finally:
        _run(["stop"], prod_home)
        _run(["stop"], source_home)
        _run(["stop"], test_home)
