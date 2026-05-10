"""hatch-being CLI entry point.

Phase 2 ships only --config mode (non-interactive replay). Phase 5 wires
the Questionary TUI as the primary UX; --config remains for tests and
reproducible hatching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher, VaultExistsError

app = typer.Typer(
    name="hatch-being",
    help="Hatch a new agent-core being. See README for usage.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def hatch_being(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Non-interactive: load HatchConfig from YAML.",
    ),
    vault_root: Optional[Path] = typer.Option(
        None,
        "--vault-root",
        "--root",
        help="Override the resolved vault root. Default: $HOME.",
    ),
    daemon_config_dir: Optional[Path] = typer.Option(
        None,
        "--daemon-config-dir",
        help="Override the daemon's config directory. Default: ~/.agent-core/.",
    ),
    init_missing: bool = typer.Option(
        False,
        "--init-missing",
        help="Top-up an existing vault with newly-added scaffolding files.",
    ),
) -> None:
    if config is None:
        typer.secho(
            "Phase 2 ships --config mode only. Wizard arrives in Phase 5.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    cfg = HatchConfig(**raw)

    if vault_root is not None:
        cfg = cfg.model_copy(update={"vault_root": str(vault_root)})
    if daemon_config_dir is not None:
        cfg = cfg.model_copy(update={"daemon_config_dir": str(daemon_config_dir)})
    if init_missing:
        cfg = cfg.model_copy(update={"init_missing": True})

    try:
        result = Hatcher(cfg).hatch()
    except VaultExistsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Hatched at {result.vault_root}")
