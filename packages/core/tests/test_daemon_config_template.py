"""Unit tests for daemon/config_template.py — minimal agent_core.yaml scaffold."""
from __future__ import annotations

from pathlib import Path

import yaml

from agent_core.daemon.config_template import build_default_config
from agent_core.daemon.instance import Instance


def test_build_default_config_prod_port(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.PROD, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["http"]["bind_port"] == 8789


def test_build_default_config_source_port(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.SOURCE, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["http"]["bind_port"] == 8788


def test_build_default_config_storage_path_under_home(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.SOURCE, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["bus"]["storage_path"] == str(tmp_path / "bus.sqlite")


def test_build_default_config_is_parseable_yaml(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.PROD, home=tmp_path)
    data = yaml.safe_load(text)  # must not raise
    assert data["http"]["bind_host"] == "127.0.0.1"
    assert isinstance(data["endpoints"], list)
    assert data["endpoints"][0]["type"] == "builtin.stub"


def test_build_default_config_test_port(tmp_path: Path) -> None:
    """TEST scaffold uses port 8787."""
    text = build_default_config(instance=Instance.TEST, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["http"]["bind_port"] == 8787


def test_build_default_config_test_storage_path_under_home(tmp_path: Path) -> None:
    """TEST scaffold storage_path is rooted at home (tmp_path here)."""
    text = build_default_config(instance=Instance.TEST, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["bus"]["storage_path"] == str(tmp_path / "bus.sqlite")


def test_build_default_config_test_has_stub_endpoint(tmp_path: Path) -> None:
    """TEST scaffold includes one builtin.stub endpoint, same shape as prod."""
    text = build_default_config(instance=Instance.TEST, home=tmp_path)
    data = yaml.safe_load(text)
    endpoints = data.get("endpoints", [])
    assert any(e.get("type") == "builtin.stub" for e in endpoints)


def test_build_default_config_test_has_qa_mcp_endpoint(tmp_path: Path) -> None:
    """TEST scaffold provisions a `qa` ClaudeCodeMCPEndpoint at /mcp/qa/.

    Provisioning the validation endpoint by default makes the
    agent-core-qa runner's transport assumption true by construction:
    the qa-runner posts MCP tool calls to `/mcp/qa/`, and a freshly-
    installed test daemon answers without any post-install config.
    """
    text = build_default_config(instance=Instance.TEST, home=tmp_path)
    data = yaml.safe_load(text)
    endpoints = data.get("endpoints", [])
    qa = next(
        (e for e in endpoints if e.get("type") == "builtin.claude_code_mcp" and e.get("name") == "qa"),
        None,
    )
    assert qa is not None, f"TEST scaffold missing qa MCP endpoint; got {endpoints}"
    assert qa["params"]["mount"] == "/mcp/qa"


def test_build_default_config_prod_has_no_qa_endpoint(tmp_path: Path) -> None:
    """PROD scaffold does NOT register `qa` — keystone is install-path identity, not config."""
    text = build_default_config(instance=Instance.PROD, home=tmp_path)
    data = yaml.safe_load(text)
    endpoints = data.get("endpoints", [])
    qa_present = any(e.get("name") == "qa" for e in endpoints)
    assert not qa_present, f"PROD scaffold unexpectedly registers qa endpoint: {endpoints}"


def test_build_default_config_source_has_no_qa_endpoint(tmp_path: Path) -> None:
    """SOURCE scaffold does NOT register `qa` — same reason as PROD."""
    text = build_default_config(instance=Instance.SOURCE, home=tmp_path)
    data = yaml.safe_load(text)
    endpoints = data.get("endpoints", [])
    qa_present = any(e.get("name") == "qa" for e in endpoints)
    assert not qa_present, f"SOURCE scaffold unexpectedly registers qa endpoint: {endpoints}"


def test_build_default_config_test_has_scheduler_endpoint(tmp_path: Path) -> None:
    """TEST scaffold includes a builtin.scheduler endpoint named 'scheduler'.

    Scenario 5 (scheduler CRUD) requires this endpoint to be present in the
    TEST daemon config so `daemon init --instance test` produces a QA-ready
    config without manual post-install editing.
    """
    text = build_default_config(instance=Instance.TEST, home=tmp_path)
    data = yaml.safe_load(text)
    endpoints = data.get("endpoints", [])
    scheduler = next(
        (e for e in endpoints if e.get("type") == "builtin.scheduler" and e.get("name") == "scheduler"),
        None,
    )
    assert scheduler is not None, (
        f"TEST scaffold missing builtin.scheduler endpoint named 'scheduler'; got {endpoints}"
    )


def test_build_default_config_test_has_discord_endpoint(tmp_path: Path) -> None:
    """TEST scaffold includes a builtin.stub endpoint named 'discord'.

    Scenario 6 (discord routing) requires a second stub endpoint named 'discord'
    in the TEST daemon config so `daemon init --instance test` produces a
    QA-ready config without manual post-install editing.
    """
    text = build_default_config(instance=Instance.TEST, home=tmp_path)
    data = yaml.safe_load(text)
    endpoints = data.get("endpoints", [])
    discord = next(
        (e for e in endpoints if e.get("type") == "builtin.stub" and e.get("name") == "discord"),
        None,
    )
    assert discord is not None, (
        f"TEST scaffold missing builtin.stub endpoint named 'discord'; got {endpoints}"
    )
