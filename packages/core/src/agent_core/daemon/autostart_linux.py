"""Linux daemon auto-start — systemd --user unit for the prod daemon.

Pure/impure split (mirrors autostart.py):
- build_systemd_unit: pure, returns the unit file content.
- install_systemd_unit: impure, writes file + calls systemctl.
- uninstall_systemd_unit: impure, calls systemctl + removes file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

UNIT_NAME = "agent-core.service"


def build_systemd_unit(*, venv_bin: Path, home: Path) -> str:
    """Return the systemd --user unit file content for the prod daemon.

    Uses Type=forking + PIDFile because daemon start forks the bus subprocess
    and exits while writing daemon.pid. Systemd monitors the forked PID.
    WatchdogSec=60 is included but requires B-1 (#304) sd_notify to activate.
    """
    pid_file = home / "daemon.pid"
    exec_bin = venv_bin / "agent-core-daemon"
    return (
        "[Unit]\n"
        "Description=agent-core prod daemon\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=forking\n"
        f"PIDFile={pid_file}\n"
        f"ExecStart={exec_bin} start --instance prod\n"
        f"ExecStop={exec_bin} stop --instance prod\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "WatchdogSec=60\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install_systemd_unit(unit_content: str, unit_path: Path) -> None:
    """Write the unit file, reload the daemon, and enable + start the service."""
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_content, encoding="utf-8")
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", UNIT_NAME],
        check=True, capture_output=True, text=True,
    )


def uninstall_systemd_unit(unit_path: Path) -> None:
    """Disable + stop the service and remove the unit file. Idempotent."""
    # Non-zero exit (unit not loaded/enabled) is not an error — suppress it.
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", UNIT_NAME],
        capture_output=True, text=True, check=False,
    )
    unit_path.unlink(missing_ok=True)
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True, text=True, check=False,
    )
