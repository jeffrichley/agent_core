"""Windows daemon auto-start — register the prod daemon as a Task Scheduler task.

Pure/impure split (project convention, mirrors build_uv_sync_command /
run_install): `build_autostart_task` is pure (returns the Task Scheduler XML),
`install_autostart` / `uninstall_autostart` are thin impure shells that run
`schtasks`.

Windows-only at the `schtasks` boundary. The pure builder is plain
string-building and works on any platform, so its tests run on any CI OS.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

TASK_NAME = "agent-core-daemon-prod"


def build_autostart_task(*, agent_core_exe: Path, account: str) -> str:
    """Return the Task Scheduler XML registering the prod daemon at logon.

    The task runs `<agent_core_exe> daemon start --instance prod` at
    `account`'s logon, with restart-on-failure (PT1M x3), start-when-
    available, ignore-new-instance, no execution time limit, least
    privilege.
    """
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>agent-core prod daemon auto-start at logon."
        "</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"      <UserId>{account}</UserId>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{account}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>3</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{agent_core_exe}</Command>\n"
        "      <Arguments>daemon start --instance prod</Arguments>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def install_autostart(xml: str) -> None:
    """Register (or replace) the autostart task from `xml`.

    Writes the XML to a temp file (UTF-16 — the Task Scheduler XML prolog
    declares UTF-16) and runs `schtasks /create /tn <TASK_NAME> /xml
    <file> /f`. Raises subprocess.CalledProcessError on a non-zero exit.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-16", suffix=".xml", delete=False
    ) as fh:
        fh.write(xml)
        xml_path = fh.name
    try:
        result = subprocess.run(
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", xml_path, "/f"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(xml_path).unlink(missing_ok=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["schtasks", "/create", "/tn", TASK_NAME, "/xml", "...", "/f"],
            output=result.stdout,
            stderr=result.stderr,
        )


def uninstall_autostart() -> bool:
    """Delete the autostart task. Returns True if a task was deleted,
    False if no such task existed. Idempotent — never raises for a
    missing task."""
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0
