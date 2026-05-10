"""Tests for the Jinja2-based template renderer."""

from pathlib import Path

import pytest

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.renderer import LeftoverBraceError, Renderer


def test_renders_jinja_substitutions(tmp_path):
    template = tmp_path / "IDENTITY.md.j2"
    template.write_text(
        "# {{ being_name }}\n"
        "Hatched: {{ hatched_date }}\n"
        "Primary human: {{ primary_human_name }}\n"
    )

    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    out = renderer.render_string(template.read_text())

    assert "# Deb" in out
    assert "Primary human: Cynthia" in out
    assert "Hatched: " in out
    assert "{{" not in out and "}}" not in out


def test_plain_md_passes_through(tmp_path):
    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    assert renderer.render_string("# Diary\n\n") == "# Diary\n\n"


def test_leftover_braces_raise():
    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    with pytest.raises(LeftoverBraceError):
        # Jinja2 happily renders {{ undefined_var }} as empty string by default;
        # the renderer must enable StrictUndefined so missing vars raise instead.
        renderer.render_string("hello {{ undefined_var }} world")


def test_path_vars_are_strings_not_PosixPath(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = HatchConfig(being_name="Deb", primary_human_name="Cynthia")
    renderer = Renderer(cfg)
    out = renderer.render_string("vault: {{ vault_root }}\n")
    # YAML-safe — no Python repr leaking through.
    assert "PosixPath" not in out
    assert "WindowsPath" not in out
    assert ".deb" in out
