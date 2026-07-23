"""agent-core venv — per-being and daemon pinned venv CLI (C2-1, issue #315)."""

from __future__ import annotations

import typer
from rich.console import Console

from agent_core.venv.builder import SidecarVerifyError, UvNotFoundError, build_being_venv

venv_app = typer.Typer(
    name="venv",
    help="Per-being and daemon pinned venv builder.",
    no_args_is_help=True,
)
console = Console()

_TARGET_ARG = typer.Argument(
    ...,
    help="Being name (e.g. 'wren', 'pepper') or 'daemon'.",
)
_PYTHON_OPT = typer.Option(
    "3.12",
    "--python",
    help="Python version for the venv.",
)


def _do_build(target: str, python_version: str) -> None:
    """Shared implementation for build and upgrade."""
    console.print(f"[bold]Building venv for[/bold] {target!r}…")
    try:
        stable = build_being_venv(target, python_version=python_version)
    except UvNotFoundError as exc:
        console.print(f"[red]uv not found[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    except SidecarVerifyError as exc:
        console.print(f"[red]sidecar verification failed[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]venv build failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]venv ready:[/green] {stable}")


@venv_app.command("build")
def build(
    target: str = _TARGET_ARG,
    python_version: str = _PYTHON_OPT,
) -> None:
    """Build (or rebuild) a pinned venv for a being or the daemon."""
    _do_build(target, python_version)


@venv_app.command("upgrade")
def upgrade(
    target: str = _TARGET_ARG,
    python_version: str = _PYTHON_OPT,
) -> None:
    """Alias for build: upgrade the pinned venv for a being or the daemon."""
    _do_build(target, python_version)


@venv_app.command("regen-mcp")
def regen_mcp(
    target: str = typer.Argument(..., help="Being name (e.g. 'wren', 'pepper')."),
) -> None:
    """Regenerate a being's canonical .mcp.json (C2-2, #316).

    Rewrites the stable-interpreter shape only if the current file is missing
    or drifted (e.g. a version-stamped path). This is the migration tool for
    moving a live being off a ``.venv-<version>`` interpreter path.
    """
    from agent_core.venv.builder import home_for_target
    from agent_core.venv.mcp_config import repair_mcp_json

    if target == "daemon":
        console.print("[red]the daemon has no .mcp.json[/red] — pass a being name.")
        raise typer.Exit(code=2)

    path, changed = repair_mcp_json(
        target,
        vault_root=home_for_target(target),
        daemon_config_dir=home_for_target("daemon"),
    )
    if changed:
        console.print(f"[green].mcp.json regenerated:[/green] {path}")
    else:
        console.print(f"[dim].mcp.json already canonical:[/dim] {path}")
