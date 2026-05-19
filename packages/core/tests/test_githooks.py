"""Unit tests for agent_core.githooks.install_git_hooks (temp git repos only)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_core.githooks import HOOKS_DIR_NAME, HookInstallError, install_git_hooks, main


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    return repo


def _make_hooks(repo: Path) -> Path:
    hooks = repo / HOOKS_DIR_NAME
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return hooks


def test_install_sets_hookspath_to_githooks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    hooks = _make_hooks(repo)

    returned = install_git_hooks(repo)

    assert returned == hooks.resolve()
    assert _git(repo, "config", "--get", "core.hooksPath") == HOOKS_DIR_NAME


def test_install_is_idempotent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _make_hooks(repo)

    install_git_hooks(repo)
    install_git_hooks(repo)  # second run must not raise

    assert _git(repo, "config", "--get", "core.hooksPath") == HOOKS_DIR_NAME


def test_install_raises_when_pre_push_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / HOOKS_DIR_NAME).mkdir()  # dir exists but pre-push absent

    with pytest.raises(HookInstallError, match="pre-push"):
        install_git_hooks(repo)


def test_main_succeeds_in_repo_with_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _init_repo(tmp_path)
    _make_hooks(repo)
    monkeypatch.chdir(repo)

    assert main() == 0
    assert _git(repo, "config", "--get", "core.hooksPath") == HOOKS_DIR_NAME
    assert "git hooks installed" in capsys.readouterr().out


def test_main_returns_1_when_hooks_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)  # no .githooks directory
    monkeypatch.chdir(repo)

    assert main() == 1


def test_committed_pre_push_runs_just_check_and_is_executable() -> None:
    """The real .githooks/pre-push must exist, invoke `just check`, and be
    tracked with git's executable mode (100755) so it runs on a fresh clone.
    """
    repo_root = next(
        p for p in Path(__file__).resolve().parents if (p / ".githooks").is_dir()
    )
    hook = repo_root / ".githooks" / "pre-push"
    assert hook.is_file(), f"{hook} missing"

    body = hook.read_text(encoding="utf-8")
    assert body.startswith("#!/bin/sh")
    assert "just check" in body
    assert "\r\n" not in body, "pre-push contains CRLF line endings (breaks on Linux CI)"

    mode = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--stage", ".githooks/pre-push"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert mode and mode[0] == "100755", f"pre-push git mode is {mode[:1]}, want 100755"
