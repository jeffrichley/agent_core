"""Unit tests for agent_core_hatchery.daemon_probe (Cβ-3, issue #327)."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.daemon_probe import (
    _probe_endpoint,
    _start_daemon,
    _stop_daemon,
    read_daemon_http_config,
    reload_and_probe,
)


def _cfg(tmp_path: Path) -> HatchConfig:
    return HatchConfig(
        being_name="Wren",
        primary_human_name="Jeff",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
    )


class TestReadDaemonHttpConfig:
    def test_reads_host_and_port_from_yaml(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text(
            "http:\n  bind_host: 0.0.0.0\n  bind_port: 9999\n",
            encoding="utf-8",
        )
        host, port = read_daemon_http_config(cfg_dir)
        assert host == "0.0.0.0"
        assert port == 9999

    def test_defaults_when_file_missing(self, tmp_path: Path) -> None:
        host, port = read_daemon_http_config(tmp_path / ".agent-core-nonexistent")
        assert host == "127.0.0.1"
        assert port == 8789

    def test_defaults_when_keys_absent(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text("bus:\n  storage_path: :memory:\n")
        host, port = read_daemon_http_config(cfg_dir)
        assert host == "127.0.0.1"
        assert port == 8789

    def test_defaults_on_yaml_error(self, tmp_path: Path) -> None:
        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text(": bad: yaml [\n")
        host, port = read_daemon_http_config(cfg_dir)
        assert host == "127.0.0.1"
        assert port == 8789


class TestStopStartDaemon:
    def test_stop_calls_agent_core_daemon_stop(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        _stop_daemon(runner=fake_runner)
        assert calls == [["agent-core", "daemon", "stop"]]

    def test_start_calls_agent_core_daemon_start(self) -> None:
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        _start_daemon(runner=fake_runner)
        assert calls == [["agent-core", "daemon", "start"]]

    def test_stop_swallows_file_not_found(self) -> None:
        def raising_runner(cmd, **kw):
            raise FileNotFoundError("agent-core not found")

        _stop_daemon(runner=raising_runner)  # must not raise

    def test_start_swallows_timeout(self) -> None:
        import subprocess

        def timeout_runner(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 15)

        _start_daemon(runner=timeout_runner)  # must not raise


class TestProbeEndpoint:
    def _make_http_response(self, status: int):
        class _FakeResp:
            def __init__(self):
                self.status = status
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
        return _FakeResp()

    def test_returns_registered_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen",
            lambda url, timeout: self._make_http_response(200),
        )
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=1.0)
        assert result == "reachable_and_registered"

    def test_returns_missing_on_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise_404(url, timeout):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen",
            _raise_404,
        )
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=1.0)
        assert result == "reachable_but_missing"

    def test_returns_registered_on_non_404_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_405(url, timeout):
            raise urllib.error.HTTPError(url, 405, "Method Not Allowed", {}, None)

        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen",
            _raise_405,
        )
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=1.0)
        assert result == "reachable_and_registered"

    def test_returns_unreachable_on_connection_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        call_count = 0

        def _refuse(url, timeout):
            nonlocal call_count
            call_count += 1
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(
            "agent_core_hatchery.daemon_probe.urllib.request.urlopen", _refuse
        )
        monkeypatch.setattr("agent_core_hatchery.daemon_probe.time.sleep", lambda s: None)

        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=0.1, poll_interval=0.0)
        assert result == "unreachable"
        assert call_count >= 1

    def test_probe_timeout_returns_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """timeout=0.0 means deadline == now; while-loop condition is False on first check."""
        monkeypatch.setattr("agent_core_hatchery.daemon_probe.time.sleep", lambda s: None)
        # urlopen is not patched — the loop body never executes when timeout=0.0
        result = _probe_endpoint("127.0.0.1", 8789, "wren", timeout=0.0, poll_interval=0.0)
        assert result == "unreachable"


class TestReloadAndProbe:
    def test_stops_then_starts_daemon(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        cfg_dir = cfg.resolved_daemon_config_dir()
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "agent_core.yaml").write_text(
            "http:\n  bind_host: 127.0.0.1\n  bind_port: 8789\n", encoding="utf-8"
        )

        call_log: list[str] = []

        def fake_runner(cmd, **kw):
            call_log.append(" ".join(cmd))
            class _R:
                returncode = 0
            return _R()

        # Stub the probe to return immediately
        with patch(
            "agent_core_hatchery.daemon_probe._probe_endpoint",
            return_value="reachable_and_registered",
        ):
            reload_and_probe(cfg, runner=fake_runner)

        assert any("stop" in c for c in call_log), f"stop not called; log={call_log}"
        assert any("start" in c for c in call_log), f"start not called; log={call_log}"
        stop_idx = next(i for i, c in enumerate(call_log) if "stop" in c)
        start_idx = next(i for i, c in enumerate(call_log) if "start" in c)
        assert stop_idx < start_idx, "stop must precede start"

    def test_returns_unreachable_when_no_config_file(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        # daemon_config_dir exists but has no agent_core.yaml
        cfg.resolved_daemon_config_dir().mkdir(parents=True)

        result = reload_and_probe(cfg)
        assert result == "unreachable"


class TestStartFailureSurfacing:
    """Adversarial review #6: a failed daemon start (fleet offline) must be a
    hard, distinct outcome — not swallowed like a best-effort stop."""

    def test_start_returns_false_on_nonzero_returncode(self) -> None:
        def runner(cmd, **kw):
            class _R:
                returncode = 3

            return _R()

        assert _start_daemon(runner=runner) is False

    def test_start_returns_true_on_success(self) -> None:
        def runner(cmd, **kw):
            class _R:
                returncode = 0

            return _R()

        assert _start_daemon(runner=runner) is True

    def test_start_returns_false_on_exception(self) -> None:
        def runner(cmd, **kw):
            raise FileNotFoundError("agent-core not on PATH")

        assert _start_daemon(runner=runner) is False

    def test_reload_and_probe_reports_start_failed_when_start_fails(self, tmp_path) -> None:
        from agent_core_hatchery.config import HatchConfig

        cfg_dir = tmp_path / ".agent-core"
        cfg_dir.mkdir()
        (cfg_dir / "agent_core.yaml").write_text(
            "http:\n  bind_host: 127.0.0.1\n  bind_port: 8789\n"
        )
        cfg = HatchConfig(
            being_name="TestBeing",
            primary_human_name="Tester",
            vault_root=str(tmp_path),
            daemon_config_dir=str(cfg_dir),
        )

        def runner(cmd, **kw):
            # stop succeeds, start fails — daemon left down.
            class _R:
                returncode = 0 if cmd[-1] == "stop" else 1

            return _R()

        assert reload_and_probe(cfg, runner=runner) == "start_failed"
