"""``agent-core briefs`` CLI subapp tests (cutover #09 / T15).

Covers the three subcommands:

* ``briefs compose`` — in-process composition (calls ``compose_brief``).
* ``briefs fetchers list`` — show registered fetchers.
* ``briefs fetchers test`` — run a single fetcher in isolation.

The tests use ``typer.testing.CliRunner`` against the top-level
``agent_core.cli.app`` so registration on the parent app is also
exercised. Stub fetcher modules are written into ``tmp_path`` directories
so each test gets an isolated, deterministic catalog.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_core.cli import app

STUB_FETCHER_SOURCE = textwrap.dedent(
    '''
    """Stub fetcher used by the briefs CLI tests."""

    from datetime import datetime


    class StubFetcher:
        type_id = "stub.test"
        namespace = "stub_ns"

        def __init__(self) -> None:
            pass

        async def fetch(self, config: dict, when: datetime) -> dict:
            return {
                "echo": config.get("echo", "default"),
                "ts": when.isoformat(),
            }
    '''
).strip()


# ---- fixtures ----


@pytest.fixture
def fetcher_dir(tmp_path: Path) -> Path:
    """Directory holding a single stub fetcher module."""
    d = tmp_path / "fetchers"
    d.mkdir()
    (d / "stub_fetcher.py").write_text(STUB_FETCHER_SOURCE + "\n", encoding="utf-8")
    return d


@pytest.fixture
def empty_fetcher_dir(tmp_path: Path) -> Path:
    """A directory with no fetchers in it."""
    d = tmp_path / "no_fetchers"
    d.mkdir()
    return d


@pytest.fixture
def gather_yaml(tmp_path: Path) -> Path:
    """A gather_config.yaml that references the stub fetcher."""
    path = tmp_path / "gather.yaml"
    path.write_text(
        textwrap.dedent(
            """
            fetchers:
              - type: stub.test
                namespace: stub_ns
                timeout_seconds: 5
                config:
                  echo: hello
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def playbook_dir(tmp_path: Path, gather_yaml: Path) -> Path:
    """Directory with a single ``cli_test.md`` playbook that points at gather_yaml."""
    d = tmp_path / "playbooks"
    d.mkdir()
    playbook = d / "cli_test.md"
    playbook.write_text(
        textwrap.dedent(
            f"""
            # CLI Test Playbook

            ```yaml
            brief_type: cli_test
            voice: "${{voice}}"
            gather_config: {gather_yaml}
            ```

            ```yaml
            destinations:
              - type: noop
                config: {{}}
            ```

            ```yaml
            colors:
              RED: 15548997
              GREEN: 5763719
            ```

            ```yaml
            section_id: greeting
            title: "Greeting"
            color: RED
            required: true
            fields:
              - name: "Today"
                required: true
            ```
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def fetcher_config_yaml(tmp_path: Path) -> Path:
    """A fetcher-config YAML for ``briefs fetchers test``."""
    path = tmp_path / "fetcher_config.yaml"
    path.write_text("echo: hi-from-test\n", encoding="utf-8")
    return path


# ---- briefs fetchers list ----


def test_fetchers_list_with_no_paths_returns_empty():
    runner = CliRunner()
    result = runner.invoke(app, ["briefs", "fetchers", "list"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed == []


def test_fetchers_list_returns_registered_fetcher(fetcher_dir: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["briefs", "fetchers", "list", "--fetcher-path", str(fetcher_dir)],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    entry = parsed[0]
    assert entry["type_id"] == "stub.test"
    assert entry["namespace"] == "stub_ns"
    assert entry["source"].endswith("stub_fetcher.py")
    assert "last_modified" in entry


def test_fetchers_list_rejects_nonexistent_path(tmp_path: Path):
    runner = CliRunner()
    bogus = tmp_path / "does_not_exist"
    result = runner.invoke(
        app,
        ["briefs", "fetchers", "list", "--fetcher-path", str(bogus)],
    )
    assert result.exit_code != 0
    assert "fetcher-path" in result.output.lower() or "does_not_exist" in result.output


# ---- briefs fetchers test ----


def test_fetchers_test_runs_fetcher_and_returns_payload(
    fetcher_dir: Path, fetcher_config_yaml: Path
):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "fetchers",
            "test",
            "--type",
            "stub.test",
            "--config",
            str(fetcher_config_yaml),
            "--fetcher-path",
            str(fetcher_dir),
            "--when",
            "2026-05-04T12:00:00+00:00",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "stub_ns" in parsed
    assert parsed["stub_ns"]["echo"] == "hi-from-test"
    assert parsed["stub_ns"]["ts"] == "2026-05-04T12:00:00+00:00"


def test_fetchers_test_unknown_type_errors(fetcher_dir: Path, fetcher_config_yaml: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "fetchers",
            "test",
            "--type",
            "stub.does-not-exist",
            "--config",
            str(fetcher_config_yaml),
            "--fetcher-path",
            str(fetcher_dir),
        ],
    )
    assert result.exit_code != 0
    assert "stub.does-not-exist" in result.output or "unknown" in result.output.lower()


def test_fetchers_test_missing_config_file_errors(fetcher_dir: Path, tmp_path: Path):
    runner = CliRunner()
    bogus = tmp_path / "nope.yaml"
    result = runner.invoke(
        app,
        [
            "briefs",
            "fetchers",
            "test",
            "--type",
            "stub.test",
            "--config",
            str(bogus),
            "--fetcher-path",
            str(fetcher_dir),
        ],
    )
    assert result.exit_code != 0
    assert "config" in result.output.lower() or "nope.yaml" in result.output


def test_fetchers_test_malformed_config_errors(fetcher_dir: Path, tmp_path: Path):
    bad = tmp_path / "list_config.yaml"
    bad.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "fetchers",
            "test",
            "--type",
            "stub.test",
            "--config",
            str(bad),
            "--fetcher-path",
            str(fetcher_dir),
        ],
    )
    assert result.exit_code != 0
    assert "mapping" in result.output.lower() or "config" in result.output.lower()


def test_fetchers_test_namespace_override(fetcher_dir: Path, fetcher_config_yaml: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "fetchers",
            "test",
            "--type",
            "stub.test",
            "--config",
            str(fetcher_config_yaml),
            "--fetcher-path",
            str(fetcher_dir),
            "--namespace",
            "overridden",
            "--when",
            "2026-05-04T12:00:00+00:00",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "overridden" in parsed
    assert "stub_ns" not in parsed


# ---- briefs compose ----


def test_compose_happy_path(fetcher_dir: Path, playbook_dir: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "compose",
            "--type",
            "cli_test",
            "--agent",
            "tester",
            "--playbooks-path",
            str(playbook_dir),
            "--fetcher-path",
            str(fetcher_dir),
            "--var",
            "voice=Friendly",
            "--when",
            "2026-05-04T08:00:00+00:00",
            "--scope",
            "today",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["brief_type"] == "cli_test"
    assert parsed["scope"] == "today"
    assert parsed["voice"] == "Friendly"
    assert "session_token" in parsed
    assert isinstance(parsed["session_token"], str)
    assert "playbook_path" in parsed
    assert "context" in parsed
    assert parsed["context"]["stub_ns"]["echo"] == "hello"


def test_compose_unknown_brief_errors(fetcher_dir: Path, playbook_dir: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "compose",
            "--type",
            "nope_brief",
            "--agent",
            "tester",
            "--playbooks-path",
            str(playbook_dir),
            "--fetcher-path",
            str(fetcher_dir),
            "--var",
            "voice=Friendly",
        ],
    )
    assert result.exit_code != 0
    assert "nope_brief" in result.output or "unknown" in result.output.lower()


def test_compose_rejects_malformed_var(fetcher_dir: Path, playbook_dir: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "briefs",
            "compose",
            "--type",
            "cli_test",
            "--agent",
            "tester",
            "--playbooks-path",
            str(playbook_dir),
            "--fetcher-path",
            str(fetcher_dir),
            "--var",
            "no_equals_sign",
        ],
    )
    assert result.exit_code != 0
    assert "var" in result.output.lower() or "KEY=VALUE" in result.output


def test_compose_rejects_missing_playbooks_path(fetcher_dir: Path, tmp_path: Path):
    runner = CliRunner()
    bogus = tmp_path / "no-playbooks-here"
    result = runner.invoke(
        app,
        [
            "briefs",
            "compose",
            "--type",
            "cli_test",
            "--agent",
            "tester",
            "--playbooks-path",
            str(bogus),
            "--fetcher-path",
            str(fetcher_dir),
            "--var",
            "voice=Friendly",
        ],
    )
    assert result.exit_code != 0
    assert "playbooks-path" in result.output.lower() or "no-playbooks-here" in result.output


def test_top_level_help_lists_briefs_subapp():
    """Sanity: ``agent-core --help`` should expose the new ``briefs`` subapp."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "briefs" in result.output
