"""Daemon reload and endpoint health-probe for hatch→run handoff (Cβ-3, issue #327).

SRP: this module does two things only — restart the daemon process and HTTP-probe the
new being's endpoint. Imports HatchConfig and DaemonCheckStatus from agent_core_hatchery;
no cycle exists (daemon_probe is not imported by hatcher.py or config.py).
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.report import DaemonCheckStatus


def read_daemon_http_config(daemon_config_dir: Path) -> tuple[str, int]:
    """Parse agent_core.yaml for the bus HTTP bind host and port.

    Returns (bind_host, bind_port). Falls back to ("127.0.0.1", 8789) if
    the file does not exist or the keys are absent.
    """
    config_path = daemon_config_dir / "agent_core.yaml"
    if not config_path.is_file():
        return ("127.0.0.1", 8789)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ("127.0.0.1", 8789)
    http = raw.get("http", {}) if isinstance(raw, dict) else {}
    host = str(http.get("bind_host", "127.0.0.1"))
    port = int(http.get("bind_port", 8789))
    return (host, port)


def _stop_daemon(runner=subprocess.run) -> None:
    """Best-effort daemon stop. Never raises; failures are absorbed."""
    try:
        runner(
            ["agent-core", "daemon", "stop"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        pass  # agent-core not on PATH or daemon already stopped — probe decides outcome


def _start_daemon(runner=subprocess.run) -> bool:
    """Start the daemon. Returns True iff the start command exited 0.

    Unlike ``_stop_daemon``, a failed START is NOT best-effort: the daemon was
    just stopped, so a failure here leaves the whole fleet offline. The result
    is returned (not swallowed) so the caller can surface it loudly.
    """
    try:
        proc = runner(
            ["agent-core", "daemon", "start"],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _probe_endpoint(
    host: str,
    port: int,
    endpoint_name: str,
    *,
    timeout: float = 15.0,
    poll_interval: float = 0.5,
) -> DaemonCheckStatus:
    """Poll http://{host}:{port}/mcp/{endpoint_name}/ until a response or timeout.

    Returns:
      "reachable_and_registered" — any non-404 HTTP response (endpoint is mounted).
      "reachable_but_missing"    — HTTP 404 (daemon up; endpoint not registered).
      "unreachable"              — polling timed out or connection always refused.
    """
    url = f"http://{host}:{port}/mcp/{endpoint_name}/"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as _resp:
                # Any 2xx or redirect means the endpoint is mounted.
                return "reachable_and_registered"
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "reachable_but_missing"
            # Other HTTP error (405, 500, etc.) — server is up, endpoint mounted.
            return "reachable_and_registered"
        except (urllib.error.URLError, OSError):
            # Connection refused / reset — daemon not yet up. Retry.
            time.sleep(poll_interval)

    return "unreachable"


def reload_and_probe(
    config: HatchConfig,
    *,
    timeout: float = 15.0,
    runner=subprocess.run,
) -> DaemonCheckStatus:
    """Stop the daemon, start it, then probe the new being's endpoint.

    Returns DaemonCheckStatus: see report.py for semantics. Called from cli.py after
    Hatcher.hatch() succeeds (fragments + venv + .mcp.json already written).
    """
    daemon_config_dir = config.resolved_daemon_config_dir()

    # Bail early if there is no daemon config to read.
    if not (daemon_config_dir / "agent_core.yaml").is_file():
        return "unreachable"

    host, port = read_daemon_http_config(daemon_config_dir)

    _stop_daemon(runner)
    if not _start_daemon(runner):
        # The daemon was stopped but could not be brought back up — the whole
        # fleet is offline. Surface as a hard failure, not a soft "unreachable"
        # (which reads as "daemon up, endpoint will register shortly").
        return "start_failed"

    return _probe_endpoint(
        host,
        port,
        config.endpoint_name,
        timeout=timeout,
        poll_interval=0.5,
    )
