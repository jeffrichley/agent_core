"""CLI tests for agent_core.venv (C2-1, issue #315)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.venv.cli import venv_app


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def _patch_build(monkeypatch: pytest.MonkeyPatch, *, raises=None, returns=None):
    """Monkeypatch build_being_venv in the CLI module."""
    def fake_build(target, *, python_version="3.12", runner=None):
        if raises is not None:
            raise raises
        return returns or Path("/fake/.wren/.venv")

    monkeypatch.setattr("agent_core.venv.cli.build_being_venv", fake_build)


class TestVenvBuildCommand:
    def test_happy_path_exits_zero(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build(monkeypatch, returns=Path("/home/user/.wren/.venv"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 0
        assert "venv ready" in result.output

    def test_uv_not_found_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_core.venv.builder import UvNotFoundError

        _patch_build(monkeypatch, raises=UvNotFoundError("uv not found"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 1
        assert "uv not found" in result.output

    def test_sidecar_verify_failure_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_core.venv.builder import SidecarVerifyError

        _patch_build(monkeypatch, raises=SidecarVerifyError("import failed"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 1
        assert "sidecar verification failed" in result.output

    def test_unexpected_error_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_build(monkeypatch, raises=RuntimeError("something exploded"))
        result = cli_runner.invoke(venv_app, ["build", "wren"])
        assert result.exit_code == 1
        assert "venv build failed" in result.output


class TestVenvUpgradeCommand:
    def test_upgrade_is_alias_for_build(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called_with: list[str] = []

        def fake_build(target, *, python_version="3.12", runner=None):
            called_with.append(target)
            return Path("/home/user/.wren/.venv")

        monkeypatch.setattr("agent_core.venv.cli.build_being_venv", fake_build)
        result = cli_runner.invoke(venv_app, ["upgrade", "pepper"])
        assert result.exit_code == 0
        assert called_with == ["pepper"]

    def test_upgrade_uv_not_found_exits_one(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_core.venv.builder import UvNotFoundError

        _patch_build(monkeypatch, raises=UvNotFoundError("uv not found"))
        result = cli_runner.invoke(venv_app, ["upgrade", "daemon"])
        assert result.exit_code == 1


class TestRegenMcpCommand:
    def test_daemon_target_rejected(
        self, cli_runner: CliRunner
    ) -> None:
        result = cli_runner.invoke(venv_app, ["regen-mcp", "daemon"])
        assert result.exit_code == 2
        assert "no .mcp.json" in result.output

    def test_regenerates_and_reports(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = cli_runner.invoke(venv_app, ["regen-mcp", "wren"])
        assert result.exit_code == 0
        assert "regenerated" in result.output
        assert (tmp_path / ".wren" / ".mcp.json").is_file()

    def test_canonical_reports_no_change(
        self, cli_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cli_runner.invoke(venv_app, ["regen-mcp", "wren"])
        result = cli_runner.invoke(venv_app, ["regen-mcp", "wren"])
        assert result.exit_code == 0
        assert "already canonical" in result.output
