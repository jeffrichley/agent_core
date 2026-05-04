"""Var substitution + path expansion: ${var} references resolve from a vars
map; ~/ expands to the user home; missing vars raise loud."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_core_briefs.config import (
    ConfigSubstitutionError,
    expand_path,
    substitute_vars,
)


def test_substitute_simple_var():
    result = substitute_vars("${agent_root}/playbooks/", {"agent_root": "/home/jeffr/.pepper"})
    assert result == "/home/jeffr/.pepper/playbooks/"


def test_substitute_multiple_vars():
    result = substitute_vars(
        "${root}/${subdir}/file",
        {"root": "/data", "subdir": "playbooks"},
    )
    assert result == "/data/playbooks/file"


def test_substitute_in_nested_dict():
    config = {
        "playbook_paths": ["${agent_root}/Memory/playbooks/"],
        "fetchers": [
            {"type": "filesystem_read", "config": {"path": "${agent_root}/TASKS.md"}},
        ],
    }
    result = substitute_vars(config, {"agent_root": "/home/jeffr/.pepper"})
    assert result == {
        "playbook_paths": ["/home/jeffr/.pepper/Memory/playbooks/"],
        "fetchers": [
            {"type": "filesystem_read", "config": {"path": "/home/jeffr/.pepper/TASKS.md"}},
        ],
    }


def test_undefined_var_raises_loud():
    with pytest.raises(ConfigSubstitutionError, match="undefined.*missing_var"):
        substitute_vars("${missing_var}/path", {})


def test_value_without_substitution_passes_through():
    assert substitute_vars("/absolute/path", {}) == "/absolute/path"
    assert substitute_vars(42, {"x": "y"}) == 42
    assert substitute_vars(None, {}) is None


def _force_home(monkeypatch, home: Path) -> None:
    """Cross-platform: ``Path.expanduser`` consults ``USERPROFILE``/``HOMEDRIVE``
    on Windows in preference to ``HOME``. Override all of them so the test
    sees ``home`` regardless of platform.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    drive, _, tail = str(home).partition(":")
    if tail:
        monkeypatch.setenv("HOMEDRIVE", f"{drive}:")
        monkeypatch.setenv("HOMEPATH", tail)


def test_expand_path_handles_user_home(tmp_path, monkeypatch):
    _force_home(monkeypatch, tmp_path)
    result = expand_path("~/.pepper/playbooks/")
    assert result == tmp_path / ".pepper" / "playbooks"


def test_expand_path_returns_path_object():
    result = expand_path("/absolute/path")
    assert isinstance(result, Path)
    assert result == Path("/absolute/path")


def test_substitute_then_expand_round_trips(tmp_path, monkeypatch):
    _force_home(monkeypatch, tmp_path)
    substituted = substitute_vars("${agent_root}/Memory/", {"agent_root": "~/.pepper"})
    expanded = expand_path(substituted)
    assert expanded == tmp_path / ".pepper" / "Memory"
