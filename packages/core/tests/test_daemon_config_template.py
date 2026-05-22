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


def test_build_default_config_dev_port(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.DEV, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["http"]["bind_port"] == 8788


def test_build_default_config_storage_path_under_home(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.DEV, home=tmp_path)
    data = yaml.safe_load(text)
    assert data["bus"]["storage_path"] == str(tmp_path / "bus.sqlite")


def test_build_default_config_is_parseable_yaml(tmp_path: Path) -> None:
    text = build_default_config(instance=Instance.PROD, home=tmp_path)
    data = yaml.safe_load(text)  # must not raise
    assert data["http"]["bind_host"] == "127.0.0.1"
    assert isinstance(data["endpoints"], list)
    assert data["endpoints"][0]["type"] == "builtin.stub"
