"""Tests for the Pipeline class — the core engine of the hook tool system."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_core.hooks.pipeline import Pipeline
from agent_core.models import ToolResult


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    config = {
        "pipelines": {
            "SessionStart": [
                {
                    "type": "builtin.time_injector",
                    "params": {"format": "%Y-%m-%d"},
                }
            ],
            "PreToolUse": [
                {"type": "builtin.time_injector"},
            ],
        }
    }
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


@pytest.fixture
def empty_config_file(tmp_path: Path) -> Path:
    config = {"pipelines": {}}
    config_path = tmp_path / "agent_core.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


class TestPipelineLoad:
    def test_load_valid_config(self, config_file: Path):
        pipeline = Pipeline(config_file)
        assert "SessionStart" in pipeline.config.pipelines
        assert len(pipeline.config.pipelines["SessionStart"]) == 1

    def test_load_empty_config(self, empty_config_file: Path):
        pipeline = Pipeline(empty_config_file)
        assert pipeline.config.pipelines == {}

    def test_load_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            Pipeline(tmp_path / "nonexistent.yaml")

    def test_load_invalid_yaml_raises(self, tmp_path: Path):
        bad_config = tmp_path / "agent_core.yaml"
        bad_config.write_text("pipelines:\n  - not: valid: yaml: [[", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            Pipeline(bad_config)


class TestPipelineRun:
    def test_run_returns_tool_results(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert isinstance(results[0], ToolResult)
        assert results[0].heading == "Current Time"

    def test_run_unregistered_event_returns_empty(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = pipeline.run("PostCompact", {})
        assert results == []

    def test_run_passes_params_to_tool(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = pipeline.run("SessionStart", {})
        assert len(results[0].content) == 10

    def test_run_multiple_tools_in_order(self, tmp_path: Path):
        config = {
            "pipelines": {
                "SessionStart": [
                    {
                        "type": "builtin.time_injector",
                        "params": {"format": "%Y"},
                    },
                    {
                        "type": "builtin.time_injector",
                        "params": {"format": "%m"},
                    },
                ],
            }
        }
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 2
        assert len(results[0].content) == 4
        assert len(results[1].content) == 2

    def test_run_bad_tool_class_skips_gracefully(self, tmp_path: Path):
        config = {
            "pipelines": {
                "SessionStart": [
                    {"type": "nonexistent.module.FakeTool"},
                    {"type": "builtin.time_injector"},
                ],
            }
        }
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        pipeline = Pipeline(config_path)
        results = pipeline.run("SessionStart", {})
        assert len(results) == 1
        assert results[0].heading == "Current Time"


class TestPipelineRender:
    def test_render_single_result(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = [ToolResult(heading="Test", content="Hello world")]
        markdown = pipeline.render(results)
        assert markdown == "## Test\n\nHello world"

    def test_render_multiple_results_separated_by_dividers(self, config_file: Path):
        pipeline = Pipeline(config_file)
        results = [
            ToolResult(heading="First", content="AAA"),
            ToolResult(heading="Second", content="BBB"),
        ]
        markdown = pipeline.render(results)
        assert markdown == "## First\n\nAAA\n\n---\n\n## Second\n\nBBB"

    def test_render_empty_results(self, config_file: Path):
        pipeline = Pipeline(config_file)
        markdown = pipeline.render([])
        assert markdown == ""


class TestDiagnosticsNeverTouchStdout:
    """Claude Code reads a hook's STDOUT as the payload; diagnostics must not land there.

    These run the pipeline in a SUBPROCESS on purpose. ``capsys`` cannot test
    this property: ``logging.basicConfig`` executes at module import, so the
    handler binds whatever ``sys.stdout`` was at import time and pytest's later
    swap never sees it — an in-process assertion on ``captured.out`` passes
    identically whether the handler targets stdout or stderr, i.e. it cannot
    fail. Verified 2026-08-17 by reverting the fix and watching the in-process
    version stay green.

    A subprocess measures what the harness actually receives: real OS-level
    file descriptors.
    """

    @staticmethod
    def _run_pipeline_in_subprocess(config_path: Path) -> subprocess.CompletedProcess[str]:
        script = (
            "from pathlib import Path\n"
            "from agent_core.hooks.pipeline import Pipeline\n"
            f"p = Pipeline(Path(r'{config_path}'))\n"
            "p.run('SessionStart', {})\n"
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_stdout_receives_no_diagnostics(self, config_file: Path):
        proc = self._run_pipeline_in_subprocess(config_file)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "", (
            f"pipeline wrote {len(proc.stdout)} bytes to stdout, which Claude Code "
            f"consumes as the hook payload:\n{proc.stdout!r}"
        )

    def test_diagnostics_still_reach_stderr(self, config_file: Path):
        """Rerouted, not deleted — the config-editing reader keeps their signal."""
        proc = self._run_pipeline_in_subprocess(config_file)
        assert "SessionStart" in proc.stderr
        assert "time_injector" in proc.stderr

    def test_unknown_tool_error_keeps_stdout_clean(self, tmp_path: Path):
        """The error path is the easiest one to leave pointed at the wrong stream."""
        config = {"pipelines": {"SessionStart": [{"type": "builtin.does_not_exist"}]}}
        config_path = tmp_path / "agent_core.yaml"
        config_path.write_text(yaml.dump(config), encoding="utf-8")
        proc = self._run_pipeline_in_subprocess(config_path)
        assert proc.stdout == "", f"error path leaked to stdout: {proc.stdout!r}"
        assert "does_not_exist" in proc.stderr
