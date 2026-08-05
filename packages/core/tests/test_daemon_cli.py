"""Tests for `agent-core daemon` CLI."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agent_core.daemon.cli import (
    _config_path,
    _daemon_python,
)
from agent_core.daemon.cli import (
    app as daemon_app,
)
from agent_core.daemon.supervisor import is_alive, read_pid

runner = CliRunner()


def test_status_when_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()


def test_stop_when_not_running_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["stop"])
    assert result.exit_code == 0


def test_config_path_is_agent_core_yaml_in_home(tmp_path: Path) -> None:
    # Behavior contract — pure function, no rendering, no terminal, no wrap.
    assert _config_path(tmp_path) == tmp_path / "agent_core.yaml"


def test_start_refuses_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Smoke test — verify the CLI refuses with exit code 1 and writes
    # SOME message. The specifics of the message (which path, what
    # instruction) are presentation; they belong in the docs and a
    # potential goldenfile test, NOT here. Earlier versions asserted on
    # the rendered "agent_core.yaml" substring and flaked under xdist
    # because Typer/Rich wrapped the path mid-token under a narrow
    # terminal width. The path contract itself is covered by
    # test_config_path_is_agent_core_yaml_in_home above.
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["start"])
    assert result.exit_code == 1
    assert result.stdout  # something was written to the user


def test_status_with_stale_pid_reports_not_running_and_cleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("999999")  # very unlikely to be alive
    result = runner.invoke(daemon_app, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.stdout.lower()
    assert not pid_file.exists()  # stale file cleaned


@pytest.mark.slow
def test_start_writes_pid_file_and_stop_kills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end: write a config, daemon start, daemon stop. Real subprocess."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
endpoints:
  - type: builtin.stub
    name: stub
""",
        encoding="utf-8",
    )

    start_res = runner.invoke(daemon_app, ["start"])
    assert start_res.exit_code == 0
    pid_file = tmp_path / "daemon.pid"
    assert pid_file.exists()
    pid = read_pid(pid_file)
    assert pid is not None

    # Give the daemon a moment to come up.
    for _ in range(40):
        if is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is True

    # Second start refuses.
    again = runner.invoke(daemon_app, ["start"])
    assert again.exit_code == 1
    assert str(pid) in again.stdout

    # Stop kills it cleanly.
    stop_res = runner.invoke(daemon_app, ["stop"])
    assert stop_res.exit_code == 0
    for _ in range(40):
        if not is_alive(pid):
            break
        time.sleep(0.1)
    assert is_alive(pid) is False
    assert not pid_file.exists()


def test_daemon_python_prod_falls_back_to_sys_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    from agent_core.daemon.instance import Instance

    # No prod .venv exists in tmp_path -> fallback to sys.executable.
    assert _daemon_python(Instance.PROD, tmp_path) == sys.executable


def test_daemon_python_prod_uses_prod_venv_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    from agent_core.daemon.instance import Instance

    if sys.platform == "win32":
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("# placeholder")

    assert _daemon_python(Instance.PROD, tmp_path) == str(venv_python)


def test_install_refuses_when_daemon_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))  # current process is alive
    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 1
    assert "currently running" in result.stdout.lower()
    assert "refresh" in result.stdout.lower()


def test_refresh_calls_stop_install_start_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop(instance: str | None = None) -> None:
        order.append("stop")

    def fake_install(instance: str | None = None, release: str | None = None) -> None:
        order.append(f"install:release={release}")

    def fake_start(instance: str | None = None) -> None:
        order.append("start")

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh", "--release", "v0.1.0"])
    assert result.exit_code == 0, result.stdout
    assert order == ["stop", "install:release=v0.1.0", "start"]


def test_refresh_aborts_start_when_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop(instance: str | None = None) -> None:
        order.append("stop")

    def fake_install(instance: str | None = None, release: str | None = None) -> None:
        order.append("install")
        raise typer.Exit(code=1)

    def fake_start(instance: str | None = None) -> None:
        order.append("start")  # must not be called

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh"])
    assert result.exit_code != 0
    assert "start" not in order
    assert order == ["stop", "install"]


def test_install_release_orchestrates_full_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install --release vX.Y.Z fetches, downloads, installs, and stamps."""
    import json
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    fake_release_json = json.dumps({
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "agent_core-0.2.0-py3-none-any.whl",
             "browser_download_url": "https://example/agent_core-0.2.0.whl"},
            {"name": "requirements.txt",
             "browser_download_url": "https://example/requirements.txt"},
        ],
    }).encode("utf-8")

    calls: list[list[str]] = []

    def fake_fetcher(url: str) -> bytes:
        if url.endswith("/releases/tags/v0.2.0"):
            return fake_release_json
        if url.endswith(".whl"):
            return b"FAKE_WHEEL_BYTES"
        if url.endswith("/requirements.txt"):
            return b"# pinned\ntorch==2.12.0+cu130\n"
        raise RuntimeError(f"unexpected URL: {url}")

    def fake_subprocess_run(cmd, **kwargs):
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.release._default_fetcher", fake_fetcher)
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_subprocess_run)

    result = runner.invoke(daemon_app, ["install", "--release", "v0.2.0"])

    # At least 3 uv invocations (uv venv, uv pip install --requirement, uv pip install --no-deps)
    assert result.exit_code == 0, result.stdout
    uv_calls = [c for c in calls if c and c[0] == "uv"]
    assert len(uv_calls) >= 2, f"expected at least 2 uv calls, got: {uv_calls}"
    assert any("--requirement" in c for c in uv_calls), "requirements.txt install missing"
    assert any("--no-deps" in c for c in uv_calls), "wheel surgical install missing"
    # Stamp written with the right version + tag
    stamp_text = (tmp_path / ".daemon-install-stamp.json").read_text()
    stamp = json.loads(stamp_text)
    assert stamp["installed_version"] == "0.2.0"
    assert stamp["release_tag"] == "v0.2.0"


# Phase 3 removed the status "fallback" warning — status is now factual
# only. The B2 false-positive it guarded against is moot.


def test_status_shows_stamp_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_core.daemon.install import InstallStamp, write_stamp

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-05-15T19:31:04Z",
            installed_sha="abc1234",
            installed_version="0.1.0",
            python_version="3.12",
            extra="cu130",
            release_tag="v0.1.0",
        ),
    )

    result = runner.invoke(daemon_app, ["status"])
    assert "abc1234" in result.stdout
    assert "2026-05-15" in result.stdout


# test_status_flags_lock_drift removed in Phase 2.5: lock-drift check is gone
# (the daemon is no longer source-installed from a workspace lock; releases
# carry their own pinned requirements.txt).

# test_status_handles_missing_workspace_gracefully removed in Phase 2.5:
# status no longer touches workspace at all (no lock-drift check, no
# find_workspace_root call from cli.py status command).


def test_refresh_source_is_stop_then_start_no_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`refresh --instance source` bounces (stop + start), never install."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    order: list[str] = []

    def fake_stop(instance: str | None = None) -> None:
        order.append("stop")

    def fake_install(instance: str | None = None, release: str | None = None) -> None:
        order.append("install")  # must NOT be called for source

    def fake_start(instance: str | None = None) -> None:
        order.append("start")

    monkeypatch.setattr("agent_core.daemon.cli.stop", fake_stop)
    monkeypatch.setattr("agent_core.daemon.cli.install", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start", fake_start)

    result = runner.invoke(daemon_app, ["refresh", "--instance", "source"])
    assert result.exit_code == 0, result.stdout
    assert order == ["stop", "start"]
    assert "install" not in order


def test_install_test_instance_succeeds_via_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon install --instance test routes through the prod install path
    against the test home. Mock release.py to verify the install was attempted
    against the test home, not prod's."""
    import json

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path / "test_home"))

    fake_release_json = json.dumps({
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "agent_core-0.2.0-py3-none-any.whl",
             "browser_download_url": "https://example/agent_core-0.2.0.whl"},
            {"name": "requirements.txt",
             "browser_download_url": "https://example/requirements.txt"},
        ],
    }).encode("utf-8")

    def fake_fetcher(url: str) -> bytes:
        if url.endswith("/releases/tags/v0.2.0"):
            return fake_release_json
        if url.endswith(".whl"):
            return b"FAKE_WHEEL_BYTES"
        if url.endswith("/requirements.txt"):
            return b"# pinned\n"
        raise RuntimeError(f"unexpected URL: {url}")

    def fake_subprocess_run(cmd, **kwargs):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr("agent_core.daemon.release._default_fetcher", fake_fetcher)
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_subprocess_run)

    result = runner.invoke(
        daemon_app,
        ["install", "--instance", "test", "--release", "v0.2.0"],
    )
    assert result.exit_code == 0, result.output
    # Stamp written — verifies the install completed against test_home
    stamp_path = tmp_path / "test_home" / ".daemon-install-stamp.json"
    assert stamp_path.exists(), f"stamp not found at {stamp_path}"
    stamp = json.loads(stamp_path.read_text())
    assert stamp["release_tag"] == "v0.2.0"


def test_install_source_instance_errors() -> None:
    """daemon install --instance source remains a deliberate error (renamed
    from --instance dev). Source runs editable from workspace .venv; nothing
    to install."""
    result = runner.invoke(
        daemon_app, ["install", "--instance", "source", "--release", "v0.2.0"]
    )
    assert result.exit_code != 0
    assert "source" in result.output.lower()
    assert "--instance dev" not in result.output


def test_unknown_instance_dev_parse_error() -> None:
    """Hard cutover: --instance dev parses as an unknown value, with a clear
    error message naming the new {prod, source, test} choice set."""
    result = runner.invoke(daemon_app, ["start", "--instance", "dev"])
    assert result.exit_code != 0
    msg = result.output.lower()
    assert "dev" in msg
    assert "prod" in msg or "source" in msg or "test" in msg


def test_install_writes_venv_path_to_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon install records venv_path = str(home / '.venv') in the install stamp."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    # Monkeypatch away GitHub and subprocess calls
    monkeypatch.setattr("agent_core.daemon.cli.resolve_version", lambda r, repo: "v0.8.0")
    monkeypatch.setattr("agent_core.daemon.cli.list_release_wheels", lambda t, repo: [("core.whl", "url")])
    monkeypatch.setattr("agent_core.daemon.cli.download_wheels", lambda a, dest: [tmp_path / "core.whl"])
    monkeypatch.setattr("agent_core.daemon.cli.download_requirements", lambda t, repo, dest: tmp_path / "requirements.txt")
    monkeypatch.setattr("agent_core.daemon.cli.ensure_venv", lambda venv, python_version: None)
    monkeypatch.setattr("agent_core.daemon.cli.install_requirements", lambda req, venv_python: None)
    monkeypatch.setattr("agent_core.daemon.cli.install_wheels", lambda wheels, venv_python: None)
    monkeypatch.setattr("agent_core.daemon.cli._git_sha_of_tag", lambda tag: "abc1234")

    result = runner.invoke(daemon_app, ["install"])
    assert result.exit_code == 0, result.stdout

    from agent_core.daemon.install import read_stamp
    stamp = read_stamp(tmp_path)
    assert stamp is not None
    assert stamp.venv_path == str(tmp_path / ".venv")


def test_doctor_reports_config_hygiene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor runs the config hygiene pass and reports findings."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.config_hygiene import HygieneReport
    from agent_core.daemon.venv_gc import VenvGcReport

    fake_report = HygieneReport(
        debris_found=[tmp_path / "agent_core.yaml.bak"],
        debris_removed=[],
        drift_messages=["fragment 'bad.yaml': reserved key(s) ['bus']"],
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: fake_report,
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: VenvGcReport(),
    )

    result = runner.invoke(daemon_app, ["doctor"])
    # doctor exits 1 when issues are found
    assert result.exit_code == 1
    assert "debris" in result.stdout.lower() or "agent_core.yaml.bak" in result.stdout
    assert "drift" in result.stdout.lower() or "bad.yaml" in result.stdout


def test_doctor_clean_config_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor exits 0 and shows clean report when no issues found."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.config_hygiene import HygieneReport
    from agent_core.daemon.venv_gc import VenvGcReport

    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: HygieneReport(),
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: VenvGcReport(),
    )

    result = runner.invoke(daemon_app, ["doctor"])
    assert result.exit_code == 0
    assert "no debris" in result.stdout.lower()
    assert "no drift" in result.stdout.lower()


def test_doctor_fix_shows_removed_debris(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor --fix shows 'removed' for deleted debris files."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.config_hygiene import HygieneReport
    from agent_core.daemon.venv_gc import VenvGcReport

    bak = tmp_path / "agent_core.yaml.bak"
    fake_report = HygieneReport(
        debris_found=[bak],
        debris_removed=[bak],
        drift_messages=[],
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: fake_report,
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: VenvGcReport(),
    )

    result = runner.invoke(daemon_app, ["doctor", "--fix"])
    assert result.exit_code == 1  # has_issues is True (debris_found is populated)
    assert "removed" in result.stdout.lower()


def test_doctor_reports_venv_gc_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor runs the venv GC pass and prints all five finding categories."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    # Populate all five finding categories so every branch in the venv GC section is covered.
    fake_venv_report = VenvGcReport(
        dead_central_corpses=[tmp_path / ".venv-v0.7.0"],
        superseded_venvs=[tmp_path / "venvs" / "0.6.0"],
        broken_stable_links=[tmp_path / ".venv"],
        orphaned_partial_builds=[tmp_path / "venvs" / "0.5.0"],
        drifted_mcp_jsons=[tmp_path / ".wren" / ".mcp.json"],
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor"])
    assert result.exit_code == 1  # has_issues → non-zero
    output = result.stdout
    assert "dead corpse" in output.lower() or ".venv-v0.7.0" in output
    assert "superseded" in output.lower() or "0.6.0" in output
    assert "broken stable link" in output.lower() or ".venv" in output
    assert "orphaned partial build" in output.lower() or "0.5.0" in output
    assert "drifted .mcp.json" in output.lower() or ".mcp.json" in output


def test_doctor_fix_removes_dead_corpses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor --fix calls remove_dead_central_corpses for detected corpses."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    corpse_path = tmp_path / ".venv-v0.7.0"
    fake_venv_report = VenvGcReport(dead_central_corpses=[corpse_path])
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )

    removal_calls: list[list] = []

    def fake_remove(corpses: list) -> list:
        removal_calls.append(list(corpses))
        return list(corpses)

    monkeypatch.setattr(
        "agent_core.daemon.cli.remove_dead_central_corpses",
        fake_remove,
    )
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor", "--fix"])
    assert result.exit_code == 1  # has_issues remains True (corpses were found)
    assert removal_calls == [[corpse_path]]  # called exactly once with the detected corpse
    assert "removed" in result.stdout.lower()


def test_status_shows_venv_path_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon status prints venv_path when the install stamp includes it."""
    import os

    from agent_core.daemon.install import InstallStamp, write_stamp

    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    (tmp_path / "daemon.pid").write_text(str(os.getpid()))
    write_stamp(
        tmp_path,
        InstallStamp(
            installed_at="2026-07-15T10:00:00Z",
            installed_sha="abc1234",
            installed_version="0.8.0",
            python_version="3.12",
            extra=None,
            release_tag="v0.8.0",
            venv_path=str(tmp_path / ".venv"),
        ),
    )

    # Force a wide console so Rich does not wrap the long tmp path across
    # lines (Rich honors COLUMNS before terminal-size detection); the
    # newline-normalize is a belt-and-suspenders guard for any wrapping.
    # Without this the exact-substring match false-fails on narrow
    # non-TTY consoles (e.g. windows-latest CI), where the path wraps
    # mid-string.
    monkeypatch.setenv("COLUMNS", "200")
    result = runner.invoke(daemon_app, ["status"])
    normalized = result.stdout.replace("\r", "").replace("\n", "")
    assert str(tmp_path / ".venv") in normalized


def test_init_writes_config_and_refuses_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` scaffolds a config and won't overwrite without --force."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    first = runner.invoke(daemon_app, ["init"])
    assert first.exit_code == 0, first.stdout
    cfg = tmp_path / "agent_core.yaml"
    assert cfg.exists()

    # Second init without --force is refused.
    second = runner.invoke(daemon_app, ["init"])
    assert second.exit_code == 1
    assert "already exists" in second.stdout.lower()

    # With --force it succeeds.
    forced = runner.invoke(daemon_app, ["init", "--force"])
    assert forced.exit_code == 0, forced.stdout


def test_unknown_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["status", "--instance", "staging"])
    assert result.exit_code == 1
    assert "unknown instance" in result.stdout.lower()


def test_start_without_config_points_at_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start with no config tells you to run `daemon init`."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["start"])
    assert result.exit_code == 1
    assert "daemon init" in result.stdout


@pytest.mark.slow
def test_prod_and_source_daemons_coexist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prod and a source daemon run simultaneously on different ports,
    each isolated — stopping one does not disturb the other.

    Both instances are driven through the AGENT_CORE_HOME escape hatch
    (one home per call), proving the core property: two daemons, two
    homes, two ports, independent lifecycle.

    Pre-rename name was ``test_prod_and_dev_daemons_coexist``; renamed
    after main's dev→source cutover (--instance dev is now an unknown
    value, so the original ``init --instance dev`` line errored at
    parse time).
    """
    import yaml as _yaml

    prod_home = tmp_path / "prod"
    source_home = tmp_path / "source"

    def _run(args: list[str], home: Path):
        monkeypatch.setenv("AGENT_CORE_HOME", str(home))
        return runner.invoke(daemon_app, args)

    # Scaffold minimal configs.
    assert _run(["init"], prod_home).exit_code == 0
    assert _run(["init", "--instance", "source"], source_home).exit_code == 0

    # Rewrite each config to a free, distinct port to avoid clashing with
    # a real daemon on 8789/8788.
    for home, port in ((prod_home, 8991), (source_home, 8992)):
        cfg = home / "agent_core.yaml"
        data = _yaml.safe_load(cfg.read_text())
        data["http"]["bind_port"] = port
        cfg.write_text(_yaml.safe_dump(data), encoding="utf-8")

    try:
        assert _run(["start"], prod_home).exit_code == 0
        assert _run(["start"], source_home).exit_code == 0

        # Give both a moment to come up.
        for _ in range(40):
            prod_pid = read_pid(prod_home / "daemon.pid")
            source_pid = read_pid(source_home / "daemon.pid")
            if prod_pid and source_pid and is_alive(prod_pid) and is_alive(source_pid):
                break
            time.sleep(0.1)

        # Both alive.
        assert "is running" in _run(["status"], prod_home).stdout
        assert "is running" in _run(["status"], source_home).stdout

        # Stop prod — source must still be alive.
        assert _run(["stop"], prod_home).exit_code == 0
        assert "is running" in _run(["status"], source_home).stdout
        assert "not running" in _run(["status"], prod_home).stdout
    finally:
        _run(["stop"], prod_home)
        _run(["stop"], source_home)


def test_install_autostart_source_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-autostart against the source instance must fail loudly with
    a clear "prod-only" message. Source runs editable from the workspace .venv
    and is started by hand; auto-starting it would silently launch dev code at
    logon. The PR's original test used --instance dev; renamed to --instance
    source after main's dev→source cutover."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["install-autostart", "--instance", "source"])
    assert result.exit_code == 1
    assert "prod-only" in result.stdout.lower()


def test_install_autostart_errors_when_prod_exe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-autostart must refuse if the prod agent-core.exe is absent."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 1
    assert "not installed" in result.stdout.lower()


def test_install_autostart_registers_and_skips_start_with_no_start_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    # Create the prod exe so the existence check passes.
    exe = tmp_path / ".venv" / "Scripts" / "agent-core.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")

    registered: list[str] = []

    def fake_install(xml: str) -> None:
        registered.append(xml)

    started: list[str] = []

    def fake_start(instance: str | None = None) -> None:
        started.append("start")

    monkeypatch.setattr("agent_core.daemon.autostart.install_autostart", fake_install)
    monkeypatch.setattr("agent_core.daemon.cli.start_daemon", fake_start)

    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 0, result.stdout
    assert len(registered) == 1  # task registered
    assert started == []  # --no-start skipped the daemon start


def test_install_autostart_starts_with_start_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    exe = tmp_path / ".venv" / "Scripts" / "agent-core.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")

    monkeypatch.setattr(
        "agent_core.daemon.autostart.install_autostart", lambda xml: None
    )
    started: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.cli.start_daemon",
        lambda instance=None: started.append("start"),
    )

    result = runner.invoke(daemon_app, ["install-autostart", "--start"])
    assert result.exit_code == 0, result.stdout
    assert started == ["start"]


def test_uninstall_autostart_reports_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "agent_core.daemon.autostart.uninstall_autostart", lambda: True
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0
    assert "removed" in result.stdout.lower()


def test_uninstall_autostart_idempotent_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "agent_core.daemon.autostart.uninstall_autostart", lambda: False
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0  # not an error
    assert "no autostart task" in result.stdout.lower()


def test_install_autostart_linux_dispatches_to_linux_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    # Create the daemon binary so the existence check passes.
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-core-daemon").write_text("#!/bin/sh\n")
    installed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.install_systemd_unit",
        lambda content, path: installed.append(str(path)),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 0, result.stdout
    assert len(installed) == 1
    assert "agent-core.service" in installed[0]


def test_install_autostart_macos_dispatches_to_macos_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-core-daemon").write_text("#!/bin/sh\n")
    installed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.install_launchd_plist",
        lambda path, content, uid, label: installed.append(label),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 0, result.stdout
    assert installed == ["com.jeffrichley.agent-core.daemon.prod"]


def test_install_autostart_unsupported_platform_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "freebsd14")
    result = runner.invoke(daemon_app, ["install-autostart"])
    assert result.exit_code == 1
    assert "freebsd14" in result.stdout.lower() or "not supported" in result.stdout.lower()


def test_install_autostart_linux_errors_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    # No .venv/bin/agent-core-daemon created.
    result = runner.invoke(daemon_app, ["install-autostart"])
    assert result.exit_code == 1


def test_uninstall_autostart_linux_dispatches_to_linux_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    removed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.uninstall_systemd_unit",
        lambda path: removed.append(str(path)),
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0, result.stdout
    assert len(removed) == 1


def test_uninstall_autostart_macos_dispatches_to_macos_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    removed: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.uninstall_launchd_plist",
        lambda path, uid, label: (removed.append(label), True)[1],
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0, result.stdout
    assert removed == ["com.jeffrichley.agent-core.daemon.prod"]


def test_uninstall_autostart_unsupported_platform_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "freebsd14")
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 1


def test_install_autostart_linux_errors_on_systemctl_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linux install: CalledProcessError from systemctl propagates as exit 1."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "linux")
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-core-daemon").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        "agent_core.daemon.autostart_linux.install_systemd_unit",
        lambda content, path: (_ for _ in ()).throw(
            __import__("subprocess").CalledProcessError(1, ["systemctl"], stderr="Bus error")
        ),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 1
    assert "systemctl" in result.stdout.lower() or "failed" in result.stdout.lower()


def test_install_autostart_macos_errors_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS install: missing agent-core-daemon binary exits 1."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    # No .venv/bin/agent-core-daemon created.
    result = runner.invoke(daemon_app, ["install-autostart"])
    assert result.exit_code == 1
    assert "not installed" in result.stdout.lower()


def test_install_autostart_macos_errors_on_launchctl_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS install: CalledProcessError from launchctl propagates as exit 1."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-core-daemon").write_text("#!/bin/sh\n")
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.install_launchd_plist",
        lambda path, content, uid, label: (_ for _ in ()).throw(
            __import__("subprocess").CalledProcessError(1, ["launchctl"], stderr="Failed")
        ),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 1
    assert "launchctl" in result.stdout.lower() or "failed" in result.stdout.lower()


def test_uninstall_autostart_macos_reports_absent_when_not_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """macOS uninstall: when launchctl bootout reports not-loaded, prints advisory."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr(
        "agent_core.daemon.autostart_macos.uninstall_launchd_plist",
        lambda path, uid, label: False,
    )
    result = runner.invoke(daemon_app, ["uninstall-autostart"])
    assert result.exit_code == 0, result.stdout
    assert "no launchd" in result.stdout.lower() or "to remove" in result.stdout.lower()


def test_install_autostart_windows_errors_on_schtasks_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows install: CalledProcessError from schtasks propagates as exit 1."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    exe = tmp_path / ".venv" / "Scripts" / "agent-core.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(
        "agent_core.daemon.autostart.install_autostart",
        lambda xml: (_ for _ in ()).throw(
            __import__("subprocess").CalledProcessError(1, ["schtasks"], stderr="Access denied")
        ),
    )
    result = runner.invoke(daemon_app, ["install-autostart", "--no-start"])
    assert result.exit_code == 1
    assert "schtasks" in result.stdout.lower() or "failed" in result.stdout.lower()


# ---------------------------------------------------------------------------
# install-service / uninstall-service CLI tests (Windows Service; #306)
# ---------------------------------------------------------------------------


def test_install_service_source_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-service against the source instance must fail with a prod-only message."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["install-service", "--instance", "source"])
    assert result.exit_code == 1
    assert "prod-only" in result.stdout.lower()


def test_install_service_errors_when_prod_venv_python_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-service must refuse if the prod venv python.exe is absent."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    result = runner.invoke(daemon_app, ["install-service", "--no-start", "--password", "pw"])
    assert result.exit_code == 1
    assert "not installed" in result.stdout.lower()


def test_install_service_registers_and_skips_start_with_no_start_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    # Create the prod venv python so the existence check passes.
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")

    installed: list[str] = []

    def fake_install_svc(**kwargs: object) -> None:
        installed.append("installed")

    started: list[str] = []

    def fake_start(instance: str | None = None) -> None:
        started.append("start")

    monkeypatch.setattr("agent_core.daemon.cli._win_svc.install_windows_service", fake_install_svc)
    monkeypatch.setattr("agent_core.daemon.cli.start_daemon", fake_start)

    result = runner.invoke(
        daemon_app, ["install-service", "--no-start", "--password", "pw"]
    )
    assert result.exit_code == 0, result.stdout
    assert len(installed) == 1  # service registered
    assert started == []  # --no-start skipped the daemon start


def test_install_service_starts_with_start_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")

    monkeypatch.setattr(
        "agent_core.daemon.cli._win_svc.install_windows_service", lambda **kw: None
    )
    started: list[str] = []
    monkeypatch.setattr(
        "agent_core.daemon.cli.start_daemon",
        lambda instance=None: started.append("start"),
    )

    result = runner.invoke(
        daemon_app, ["install-service", "--start", "--password", "pw"]
    )
    assert result.exit_code == 0, result.stdout
    assert started == ["start"]


def test_install_service_reports_scm_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install-service catches SCM errors and exits non-zero."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")

    def failing_install(**kwargs: object) -> None:
        raise RuntimeError("SCM denied")

    monkeypatch.setattr("agent_core.daemon.cli._win_svc.install_windows_service", failing_install)

    result = runner.invoke(
        daemon_app, ["install-service", "--no-start", "--password", "pw"]
    )
    assert result.exit_code == 1
    assert "scm" in result.stdout.lower() or "failed" in result.stdout.lower()


def test_uninstall_service_reports_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "agent_core.daemon.cli._win_svc.uninstall_windows_service", lambda: True
    )
    result = runner.invoke(daemon_app, ["uninstall-service"])
    assert result.exit_code == 0
    assert "removed" in result.stdout.lower()


def test_uninstall_service_idempotent_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "agent_core.daemon.cli._win_svc.uninstall_windows_service", lambda: False
    )
    result = runner.invoke(daemon_app, ["uninstall-service"])
    assert result.exit_code == 0  # not an error
    assert "no service" in result.stdout.lower()


def test_uninstall_service_source_instance_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))
    result = runner.invoke(daemon_app, ["uninstall-service", "--instance", "source"])
    assert result.exit_code == 1
    assert "prod-only" in result.stdout.lower()


def test_doctor_fix_prunes_superseded_venvs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor --fix calls prune_superseded_venvs for detected superseded venvs."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    superseded_path = tmp_path / "venvs" / "0.6.0"
    fake_venv_report = VenvGcReport(superseded_venvs=[superseded_path])
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )
    prune_calls: list[list] = []

    def fake_prune(paths: list) -> list:
        prune_calls.append(list(paths))
        return list(paths)  # simulate all removed

    monkeypatch.setattr("agent_core.daemon.cli.prune_superseded_venvs", fake_prune)
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor", "--fix"])
    assert result.exit_code == 1  # has_issues remains True
    assert prune_calls == [[superseded_path]]
    assert "removed" in result.stdout.lower()


def test_doctor_fix_removes_broken_stable_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor --fix calls remove_broken_stable_link for each broken link."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    stable_path = tmp_path / ".venv"
    fake_venv_report = VenvGcReport(broken_stable_links=[stable_path])
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )
    removal_calls: list = []

    def fake_remove(p: Path) -> bool:
        removal_calls.append(p)
        return True  # simulate successful removal

    monkeypatch.setattr("agent_core.daemon.cli.remove_broken_stable_link", fake_remove)
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor", "--fix"])
    assert result.exit_code == 1
    assert removal_calls == [stable_path]
    assert "removed" in result.stdout.lower()


def test_doctor_fix_removes_orphaned_partial_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor --fix calls remove_orphaned_partial_builds for orphaned dirs."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    orphan_path = tmp_path / "venvs" / "0.8.0"
    fake_venv_report = VenvGcReport(orphaned_partial_builds=[orphan_path])
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )
    remove_calls: list[list] = []

    def fake_remove(paths: list) -> list:
        remove_calls.append(list(paths))
        return list(paths)

    monkeypatch.setattr("agent_core.daemon.cli.remove_orphaned_partial_builds", fake_remove)
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor", "--fix"])
    assert result.exit_code == 1
    assert remove_calls == [[orphan_path]]
    assert "removed" in result.stdout.lower()


def test_doctor_fix_repairs_drifted_mcp_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """daemon doctor --fix calls repair_mcp_json with correct being_name and vault_root."""
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path))

    from agent_core.daemon.venv_gc import VenvGcReport

    wren_home = tmp_path / ".wren"
    wren_home.mkdir()
    mcp_json_path = wren_home / ".mcp.json"
    fake_venv_report = VenvGcReport(drifted_mcp_jsons=[mcp_json_path])
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_venv_doctor",
        lambda daemon_home, home_root, daemon_config_dir: fake_venv_report,
    )
    repair_calls: list[tuple] = []

    def fake_repair(
        being_name: str, *, vault_root: Path, daemon_config_dir: Path
    ) -> tuple[Path, bool]:
        repair_calls.append((being_name, vault_root, daemon_config_dir))
        return mcp_json_path, True  # simulate repaired

    monkeypatch.setattr("agent_core.daemon.cli.repair_mcp_json", fake_repair)
    monkeypatch.setattr(
        "agent_core.daemon.cli.run_config_hygiene",
        lambda config_dir, fix: __import__(
            "agent_core.daemon.config_hygiene", fromlist=["HygieneReport"]
        ).HygieneReport(),
    )

    result = runner.invoke(daemon_app, ["doctor", "--fix"])
    assert result.exit_code == 1
    assert len(repair_calls) == 1
    being_name_called, vault_root_called, _ = repair_calls[0]
    assert being_name_called == "wren"
    assert vault_root_called == wren_home
    assert "repaired" in result.stdout.lower()
