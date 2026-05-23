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
