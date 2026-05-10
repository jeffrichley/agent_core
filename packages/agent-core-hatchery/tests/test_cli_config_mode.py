"""End-to-end test of the `hatch-being --config <yaml>` invocation."""

from pathlib import Path

from typer.testing import CliRunner

from agent_core_hatchery.cli import app


FIXTURES = Path(__file__).parent / "fixtures"


def test_config_mode_hatches_into_tmpdir(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "--config",
            str(FIXTURES / "hatch-config-test-being.yaml"),
            "--vault-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    vault = tmp_path / ".testbeing"
    assert (vault / "Memory" / "IDENTITY.md").is_file()
    assert "TestBeing" in (vault / "Memory" / "IDENTITY.md").read_text()
