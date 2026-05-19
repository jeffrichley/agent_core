"""Install this clone's version-controlled git hooks (.githooks/).

`just install-hooks` wraps `main()`. Logic lives here (not in the recipe)
so it is directly unit-testable, mirroring daemon/install.py vs
daemon/cli.py.

A relative `core.hooksPath` of ".githooks" is resolved by git relative
to the working-tree root at hook-trigger time, so it is correct for the
main checkout and every linked worktree (each has its own committed
.githooks/ directory).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HOOKS_DIR_NAME = ".githooks"
REQUIRED_HOOKS = ("pre-push",)


class HookInstallError(Exception):
    """Raised when the versioned hooks directory is missing or incomplete."""


def install_git_hooks(repo_root: Path) -> Path:
    """Point `repo_root`'s git at `<repo_root>/.githooks`. Idempotent.

    Returns the resolved hooks directory. Raises HookInstallError if
    `.githooks/pre-push` is absent, so a broken checkout fails loudly
    instead of silently disabling the gate.
    """
    repo_root = repo_root.resolve()
    hooks_dir = repo_root / HOOKS_DIR_NAME
    for hook in REQUIRED_HOOKS:
        if not (hooks_dir / hook).is_file():
            raise HookInstallError(
                f"missing {HOOKS_DIR_NAME}/{hook} under {repo_root} — "
                "run this from the agent_core repo root"
            )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "core.hooksPath", HOOKS_DIR_NAME],
        check=True,
    )
    return hooks_dir


def main() -> int:
    try:
        hooks_dir = install_git_hooks(Path.cwd())
    except HookInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        msg = f"error: git config failed (exit {exc.returncode})"
        stderr = exc.stderr
        if stderr:
            msg += f"\n{stderr.rstrip()}"
        print(msg, file=sys.stderr)
        return 1
    print(f"git hooks installed: core.hooksPath -> {HOOKS_DIR_NAME} ({hooks_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
