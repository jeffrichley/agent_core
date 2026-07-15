"""macOS daemon auto-start — launchd LaunchAgent plist for the prod daemon.

Pure/impure split (mirrors autostart.py):
- build_launchd_plist: pure, returns the plist XML.
- install_launchd_plist: impure, writes file + calls launchctl.
- uninstall_launchd_plist: impure, calls launchctl + removes file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LABEL = "com.jeffrichley.agent-core.daemon.prod"


def build_launchd_plist(*, venv_bin: Path, home: Path, label: str, uid: int) -> str:
    """Return the launchd LaunchAgent plist XML for the prod daemon.

    KeepAlive=true: launchd respawns the job if it exits.
    RunAtLoad=true: job starts immediately when the plist is bootstrapped.
    Note: `uid` is accepted for future use (could appear in ProgramArguments
    or environment); currently used only by the impure installer.
    """
    exec_bin = venv_bin / "agent-core-daemon"
    log_path = home / "daemon.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"\n'
        '          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{label}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{exec_bin}</string>\n"
        "        <string>start</string>\n"
        "        <string>--instance</string>\n"
        "        <string>prod</string>\n"
        "    </array>\n"
        "    <key>KeepAlive</key>\n"
        "    <true/>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{log_path}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def install_launchd_plist(
    plist_path: Path,
    plist_content: str,
    *,
    uid: int,
    label: str,
) -> None:
    """Write the plist and bootstrap it into the user launchd session. Idempotent."""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content, encoding="utf-8")
    # Idempotent unload — ignore non-zero exit (service not currently loaded).
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True, text=True, check=False,
    )
    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        check=True, capture_output=True, text=True,
    )


def uninstall_launchd_plist(
    plist_path: Path,
    *,
    uid: int,
    label: str,
) -> bool:
    """Unload and remove the launchd plist. Returns True if bootout succeeded."""
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}/{label}"],
        capture_output=True, text=True, check=False,
    )
    plist_path.unlink(missing_ok=True)
    return result.returncode == 0
