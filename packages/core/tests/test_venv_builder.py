"""Unit tests for agent_core.venv.builder (C2-1, issue #315)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent_core.venv.builder import (
    SIDECAR_PACKAGES,
    SidecarVerifyError,
    UvNotFoundError,
    atomic_repoint,
    build_being_venv,
    create_venv,
    home_for_target,
    install_sidecars,
    python_in_venv,
    resolve_uv,
    stable_venv_path,
    verify_sidecars,
    versioned_venv_dir,
)

# ---------------------------------------------------------------------------
# resolve_uv
# ---------------------------------------------------------------------------

class TestResolveUv:
    def test_finds_cargo_bin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tmp_path / ".cargo" / "bin" / f"uv{suffix}"
        uv.parent.mkdir(parents=True)
        uv.write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert resolve_uv() == uv.resolve()

    def test_finds_local_bin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tmp_path / ".local" / "bin" / f"uv{suffix}"
        uv.parent.mkdir(parents=True)
        uv.write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert resolve_uv() == uv.resolve()

    def test_falls_back_to_which(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        uv_on_path = tmp_path / "uv"
        uv_on_path.write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: empty_home))
        monkeypatch.setattr("agent_core.venv.builder.shutil.which", lambda _: str(uv_on_path))
        assert resolve_uv() == uv_on_path.resolve()

    def test_raises_with_install_hint_when_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty_home = tmp_path / "home"
        empty_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: empty_home))
        monkeypatch.setattr("agent_core.venv.builder.shutil.which", lambda _: None)
        with pytest.raises(UvNotFoundError, match="astral.sh/uv"):
            resolve_uv()


# ---------------------------------------------------------------------------
# Path layout helpers
# ---------------------------------------------------------------------------

class TestPathLayout:
    def test_home_for_daemon(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert home_for_target("daemon") == tmp_path / ".agent-core"

    def test_home_for_being(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert home_for_target("wren") == tmp_path / ".wren"

    def test_versioned_venv_dir_being(self, tmp_path: Path) -> None:
        home = tmp_path / ".wren"
        result = versioned_venv_dir(home, "0.8.0", target="wren")
        assert result == home / ".agent-core" / "venvs" / "0.8.0"

    def test_versioned_venv_dir_daemon_no_nested_agent_core(self, tmp_path: Path) -> None:
        home = tmp_path / ".agent-core"
        result = versioned_venv_dir(home, "0.8.0", target="daemon")
        # Must NOT produce ~/.agent-core/.agent-core/venvs/…
        assert result == home / "venvs" / "0.8.0"
        assert ".agent-core" not in str(result.relative_to(home))

    def test_stable_venv_path(self, tmp_path: Path) -> None:
        assert stable_venv_path(tmp_path / ".wren") == tmp_path / ".wren" / ".venv"

    def test_python_in_venv_posix(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_core.venv.builder.sys.platform", "linux")
        assert python_in_venv(tmp_path) == tmp_path / "bin" / "python"

    def test_python_in_venv_windows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("agent_core.venv.builder.sys.platform", "win32")
        assert python_in_venv(tmp_path) == tmp_path / "Scripts" / "python.exe"


# ---------------------------------------------------------------------------
# create_venv
# ---------------------------------------------------------------------------

class TestCreateVenv:
    def test_invokes_uv_venv_when_python_absent(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_dir = tmp_path / "venv"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        create_venv(uv, venv_dir, python_version="3.12", runner=fake_runner)

        assert len(calls) == 1
        assert str(uv) in calls[0]
        assert "venv" in calls[0]
        assert str(venv_dir) in calls[0]
        assert "--python" in calls[0] and "3.12" in calls[0]

    def test_no_op_when_python_exists(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_dir = tmp_path / "venv"
        # Pre-create the python binary
        py = python_in_venv(venv_dir)
        py.parent.mkdir(parents=True)
        py.write_text("")

        calls: list = []
        def fake_runner(cmd, **kw):
            calls.append(cmd)
            class _R:
                returncode = 0
            return _R()

        create_venv(uv, venv_dir, runner=fake_runner)
        assert calls == []

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_dir = tmp_path / "a" / "b" / "c"

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
            return _R()

        create_venv(uv, venv_dir, runner=fake_runner)
        assert venv_dir.parent.exists()


# ---------------------------------------------------------------------------
# install_sidecars
# ---------------------------------------------------------------------------

class TestInstallSidecars:
    def test_installs_all_three_packages(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        install_sidecars(uv, venv_python, runner=fake_runner)

        assert len(calls) == 1
        for pkg in SIDECAR_PACKAGES:
            assert pkg in calls[0]

    def test_uses_absolute_uv_path(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        install_sidecars(uv, venv_python, runner=fake_runner)
        assert calls[0][0] == str(uv)

    def test_passes_python_flag(self, tmp_path: Path) -> None:
        uv = tmp_path / "uv"
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
            return _R()

        install_sidecars(uv, venv_python, runner=fake_runner)
        cmd = calls[0]
        assert "--python" in cmd
        idx = cmd.index("--python")
        assert cmd[idx + 1] == str(venv_python)


# ---------------------------------------------------------------------------
# verify_sidecars
# ---------------------------------------------------------------------------

class TestVerifySidecars:
    def test_passes_on_zero_exit(self, tmp_path: Path) -> None:
        venv_python = tmp_path / "bin" / "python"

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        verify_sidecars(venv_python, runner=fake_runner)  # must not raise

    def test_raises_on_non_zero_exit(self, tmp_path: Path) -> None:
        venv_python = tmp_path / "bin" / "python"

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 1
                stderr = "ModuleNotFoundError: No module named 'agent_core_busproxy'"
            return _R()

        with pytest.raises(SidecarVerifyError, match="agent_core_busproxy"):
            verify_sidecars(venv_python, runner=fake_runner)

    def test_import_command_includes_all_three_modules(self, tmp_path: Path) -> None:
        venv_python = tmp_path / "bin" / "python"
        calls: list[list[str]] = []

        def fake_runner(cmd, **kw):
            calls.append(list(cmd))
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        verify_sidecars(venv_python, runner=fake_runner)

        assert len(calls) == 1
        cmd_joined = " ".join(calls[0])
        assert "agent_core_busproxy" in cmd_joined
        assert "agent_core_channel" in cmd_joined
        assert "agent_core_notify" in cmd_joined


# ---------------------------------------------------------------------------
# atomic_repoint (POSIX only)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink tests")
class TestAtomicRepointPosix:
    def test_creates_symlink(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "venvs" / "0.8.0"
        target_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"

        atomic_repoint(stable, target_dir)

        assert stable.is_symlink()
        assert os.readlink(stable) == str(target_dir)

    def test_replaces_existing_symlink(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "venvs" / "0.7.0"
        new_dir = tmp_path / "venvs" / "0.8.0"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"

        atomic_repoint(stable, old_dir)
        atomic_repoint(stable, new_dir)

        assert os.readlink(stable) == str(new_dir)

    def test_old_versioned_dir_not_removed(self, tmp_path: Path) -> None:
        """D3 — never destroy old venv; GC is C2-3's job."""
        old_dir = tmp_path / "venvs" / "0.7.0"
        new_dir = tmp_path / "venvs" / "0.8.0"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"

        atomic_repoint(stable, old_dir)
        atomic_repoint(stable, new_dir)

        assert old_dir.exists()

    def test_cleans_leftover_tmp_symlink(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "venvs" / "0.8.0"
        target_dir.mkdir(parents=True)
        stable = tmp_path / ".venv"
        stale_tmp = tmp_path / ".venv.tmp"
        os.symlink(tmp_path / "stale", stale_tmp)  # stale from prior interrupted run

        atomic_repoint(stable, target_dir)

        assert stable.is_symlink()
        assert not stale_tmp.exists()


# ---------------------------------------------------------------------------
# build_being_venv integration (monkeypatched subprocess)
# ---------------------------------------------------------------------------

class TestBuildBeingVenv:
    def _plant_uv(self, tmp_path: Path) -> Path:
        suffix = ".exe" if sys.platform == "win32" else ""
        uv = tmp_path / ".cargo" / "bin" / f"uv{suffix}"
        uv.parent.mkdir(parents=True)
        uv.write_text("")
        return uv

    def test_happy_path_returns_stable_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        monkeypatch.setattr("agent_core.venv.builder.atomic_repoint", lambda s, t: None)

        stable = build_being_venv("wren", runner=fake_runner)
        assert stable == tmp_path / ".wren" / ".venv"

    def test_three_subprocess_calls_in_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_venv → install_sidecars → verify_sidecars; atomic_repoint is last."""
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        call_log: list[str] = []

        def fake_runner(cmd, **kw):
            # Identify step by command shape
            cmd_list = list(cmd)
            if "venv" in cmd_list:
                call_log.append("create_venv")
            elif "pip" in cmd_list:
                call_log.append("install_sidecars")
            else:
                call_log.append("verify_sidecars")
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        monkeypatch.setattr("agent_core.venv.builder.atomic_repoint", lambda s, t: None)
        build_being_venv("wren", runner=fake_runner)

        assert call_log == ["create_venv", "install_sidecars", "verify_sidecars"]

    def test_verify_failure_aborts_before_repoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        repoint_calls: list = []
        monkeypatch.setattr(
            "agent_core.venv.builder.atomic_repoint",
            lambda s, t: repoint_calls.append((s, t)),
        )

        def fake_runner(cmd, **kw):
            cmd_list = list(cmd)
            # Fail the verify step
            if "venv" not in cmd_list and "pip" not in cmd_list:
                class _R:
                    returncode = 1
                    stderr = "ModuleNotFoundError"
                return _R()
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        with pytest.raises(SidecarVerifyError):
            build_being_venv("wren", runner=fake_runner)

        assert repoint_calls == [], "atomic_repoint must not be called after verify failure"

    def test_daemon_versioned_dir_has_no_nested_agent_core(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr("agent_core.venv.builder._metadata_version", lambda _: "0.8.0")
        self._plant_uv(tmp_path)

        captured_venv_dir: list[Path] = []

        def fake_runner(cmd, **kw):
            class _R:
                returncode = 0
                stderr = ""
            return _R()

        original_repoint = atomic_repoint  # noqa: F841

        def capturing_repoint(stable: Path, target_dir: Path) -> None:
            captured_venv_dir.append(target_dir)

        monkeypatch.setattr("agent_core.venv.builder.atomic_repoint", capturing_repoint)
        build_being_venv("daemon", runner=fake_runner)

        assert len(captured_venv_dir) == 1
        venv_dir = captured_venv_dir[0]
        daemon_home = tmp_path / ".agent-core"
        # Must be ~/.agent-core/venvs/0.8.0/, NOT ~/.agent-core/.agent-core/venvs/0.8.0/
        assert venv_dir == daemon_home / "venvs" / "0.8.0"
