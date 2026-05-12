"""Tests for the Questionary wizard.

Wizard prompts are mocked at the questionary boundary; we test the
validation/normalization logic, not Questionary itself.
"""

from agent_core_hatchery.wizard import _validate_being_name, _validate_endpoint_name


def test_being_name_must_be_non_empty():
    assert _validate_being_name("") is not True
    assert _validate_being_name("Deb") is True


def test_being_name_must_not_have_path_chars():
    assert _validate_being_name("../bad") is not True
    assert _validate_being_name("good-name") is True


def test_endpoint_name_must_be_kebab_friendly():
    assert _validate_endpoint_name("deb") is True
    assert _validate_endpoint_name("DEB") is not True  # uppercase should be lowered first
    assert _validate_endpoint_name("deb space") is not True
