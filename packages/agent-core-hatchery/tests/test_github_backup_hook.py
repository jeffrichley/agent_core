"""Behavioural tests for the generated github_backup hook.

These run the generated script against a real local git remote rather than
asserting on its text. The defect that motivated them (#601) was invisible to a
text assertion: the old hook ended in ``git push ... || true``, so it exited 0
whether or not it delivered anything, and the existing test -- which checked
that the repo URL appeared in the file -- passed the whole time.

The property under test is therefore: *a run that did not deliver must exit
non-zero and say so.*
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_core_hatchery.channels.github_backup import scaffold_github_backup
from agent_core_hatchery.config import (
    ChannelsConfig,
    GitHubBackupConfig,
    HatchConfig,
)
from pydantic import ValidationError

# Resolve bash to an ABSOLUTE path and invoke it that way. On Windows,
# `subprocess.run(["bash", ...])` does not use PATH order: CreateProcess
# searches the system directory first, so it finds WSL's System32\bash.exe --
# a linux-gnu bash that cannot see the Windows filesystem at all and fails
# every script with "No such file or directory" (rc=127). `shutil.which`
# meanwhile reports Git's msys bash, so the two disagree and the test appears
# to prove the hook is broken when the hook never ran.
_BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    _BASH is None or shutil.which("git") is None,
    reason="needs bash and git to execute the generated hook",
)

# Identity for the commit the hook makes. Without these a CI runner with no
# configured user.email fails the commit, and the test would go red for a
# reason that has nothing to do with what it is testing.
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _cfg(tmp_path: Path, repo_url: str) -> HatchConfig:
    """Build a hatch config whose vault resolves to ``tmp_path/.deb``."""
    return HatchConfig(
        being_name="Deb",
        primary_human_name="Cynthia",
        vault_root=str(tmp_path),
        daemon_config_dir=str(tmp_path / ".agent-core"),
        channels=ChannelsConfig(github_backup=GitHubBackupConfig(enabled=True, repo_url=repo_url)),
    )


def _run_hook(hook: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Execute the generated hook and capture its output."""
    full_env = {**os.environ, **_GIT_ENV, **env}
    assert _BASH is not None  # guarded by pytestmark
    # `as_posix()` as well as the absolute interpreter: a native Windows path
    # reaches bash with its backslashes consumed as escapes.
    return subprocess.run(
        [_BASH, hook.as_posix()],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=120,
    )


def _seed_vault(tmp_path: Path) -> Path:
    """Create a vault root with one file in Memory/ for the hook to back up."""
    memory = tmp_path / ".deb" / "Memory"
    memory.mkdir(parents=True)
    (memory / "SOUL.md").write_text("a being's memory\n", encoding="utf-8")
    return memory


def test_hook_pushes_and_verifies_against_a_real_remote(tmp_path: Path) -> None:
    """A delivering run exits 0 and leaves remote == local."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    memory = _seed_vault(tmp_path)

    scaffold_github_backup(_cfg(tmp_path, remote.as_uri()))
    hook = tmp_path / ".deb" / "hooks" / "backup-to-github.sh"
    assert hook.is_file()

    result = _run_hook(hook, {})
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "Backup verified" in result.stdout

    local = subprocess.run(
        ["git", "-C", str(memory), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    remote_sha = subprocess.run(
        ["git", "ls-remote", str(remote), "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    assert local == remote_sha


def test_hook_fails_loudly_when_the_remote_is_unreachable(tmp_path: Path) -> None:
    """A run that delivers nothing must exit non-zero and name the failure.

    This is the regression lock for #601. Against the previous template this
    case exited 0 and printed nothing alarming.
    """
    _seed_vault(tmp_path)
    unreachable = (tmp_path / "does-not-exist.git").as_uri()
    scaffold_github_backup(_cfg(tmp_path, unreachable))
    hook = tmp_path / ".deb" / "hooks" / "backup-to-github.sh"

    result = _run_hook(hook, {})
    assert result.returncode != 0, "a backup that delivered nothing reported success"
    assert "BACKUP FAILED" in result.stderr
    assert "<unreachable>" in result.stderr


def test_hook_is_idempotent_on_a_second_run(tmp_path: Path) -> None:
    """Re-running with nothing new still verifies rather than short-circuiting."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _seed_vault(tmp_path)
    scaffold_github_backup(_cfg(tmp_path, remote.as_uri()))
    hook = tmp_path / ".deb" / "hooks" / "backup-to-github.sh"

    assert _run_hook(hook, {}).returncode == 0
    second = _run_hook(hook, {})
    assert second.returncode == 0, f"stderr={second.stderr}"
    assert "Nothing to commit" in second.stdout
    assert "Backup verified" in second.stdout


def test_generated_hook_carries_no_credential(tmp_path: Path) -> None:
    """The scaffolder must never write a secret into the hook text."""
    remote = tmp_path / "remote.git"
    scaffold_github_backup(_cfg(tmp_path, remote.as_uri()))
    text = (tmp_path / ".deb" / "hooks" / "backup-to-github.sh").read_text(encoding="utf-8")
    repo_line = next(line for line in text.splitlines() if line.startswith("REPO_URL="))
    # Written verbatim, with nothing appended -- and userinfo can never reach
    # here because GitHubBackupConfig refuses it upstream.
    assert repo_line == f'REPO_URL="{remote.as_uri()}"'
    assert "ghp_" not in text


def test_config_rejects_a_repo_url_with_an_embedded_credential() -> None:
    """A tokenised URL is refused at hatch time, not persisted at 4 AM."""
    with pytest.raises(ValidationError, match="must not embed a credential"):
        GitHubBackupConfig(
            enabled=True,
            repo_url="https://user:ghp_secretsecretsecret@github.com/o/r.git",
        )


def test_config_accepts_plain_urls() -> None:
    """Plain https and ssh URLs stay valid."""
    assert GitHubBackupConfig(repo_url="https://github.com/o/r.git").repo_url
    assert GitHubBackupConfig(repo_url="git@github.com:o/r.git").repo_url
