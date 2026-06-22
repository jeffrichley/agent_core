"""Tests for the dotted-path resolver."""
from agent_core_inbound._path import MISSING, resolve_path


def test_resolves_top_level_key() -> None:
    assert resolve_path({"a": 1}, "a") == 1


def test_resolves_nested_key() -> None:
    assert resolve_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42


def test_missing_top_level_returns_missing_sentinel() -> None:
    assert resolve_path({"a": 1}, "b") is MISSING


def test_missing_nested_returns_missing_sentinel() -> None:
    assert resolve_path({"a": {"b": 1}}, "a.c") is MISSING


def test_path_through_non_dict_returns_missing() -> None:
    assert resolve_path({"a": "string"}, "a.b") is MISSING


def test_path_through_none_returns_missing() -> None:
    assert resolve_path({"a": None}, "a.b") is MISSING


def test_resolves_falsy_values_correctly() -> None:
    # False / 0 / "" / None / [] / {} all resolve to themselves, not MISSING.
    assert resolve_path({"a": False}, "a") is False
    assert resolve_path({"a": 0}, "a") == 0
    assert resolve_path({"a": ""}, "a") == ""
    assert resolve_path({"a": None}, "a") is None
    assert resolve_path({"a": []}, "a") == []
    assert resolve_path({"a": {}}, "a") == {}


def test_empty_path_returns_missing() -> None:
    # Explicitly: empty path doesn't return the root dict.
    assert resolve_path({"a": 1}, "") is MISSING


def test_missing_sentinel_repr() -> None:
    # Debug contract: repr is the string "MISSING", not the default <object>.
    assert repr(MISSING) == "MISSING"
