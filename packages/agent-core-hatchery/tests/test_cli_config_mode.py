"""End-to-end test of the `hatch-being --config <yaml>` invocation."""

from pathlib import Path
from unittest.mock import patch

from agent_core_hatchery.cli import app
from typer.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures"


def _invoke_cli(tmp_path: Path) -> object:
    """Invoke the hatch-being CLI with subprocess-heavy steps stubbed out.

    Patches out three subprocess-heavy / not-yet-shipped steps:
    - _build_being_venv (C2-1 #315 subprocess): patched to a no-op.
    - _generate_mcp_json (C2-2 #316 not yet merged): patched to a no-op.
    - reload_and_probe (Cβ-3 #327 daemon subprocess): returns "unreachable" → exit 0.
    """
    runner = CliRunner()
    with (
        patch("agent_core_hatchery.hatcher.Hatcher._build_being_venv"),
        patch("agent_core_hatchery.hatcher.Hatcher._generate_mcp_json"),
        patch("agent_core_hatchery.cli.reload_and_probe", return_value="unreachable"),
    ):
        return runner.invoke(
            app,
            [
                "--config",
                str(FIXTURES / "hatch-config-test-being.yaml"),
                "--vault-root",
                str(tmp_path),
                "--daemon-config-dir",
                str(tmp_path / ".agent-core"),
            ],
        )


def test_config_mode_hatches_into_tmpdir(tmp_path):
    result = _invoke_cli(tmp_path)
    assert result.exit_code == 0, result.stdout
    vault = tmp_path / ".testbeing"
    assert (vault / "Memory" / "IDENTITY.md").is_file()
    assert "TestBeing" in (vault / "Memory" / "IDENTITY.md").read_text(encoding="utf-8")


def test_config_mode_writes_hatching_report(tmp_path):
    result = _invoke_cli(tmp_path)
    assert result.exit_code == 0, result.stdout
    report = tmp_path / ".testbeing" / "HATCHING-REPORT.md"
    assert report.is_file()
    assert "TestBeing" in report.read_text(encoding="utf-8")
