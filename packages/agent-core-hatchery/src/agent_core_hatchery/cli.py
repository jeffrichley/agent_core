"""hatch-being CLI entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import Hatcher, VaultExistsError
from agent_core_hatchery.report import write_hatching_report
from agent_core_hatchery.wizard import offer_letter_authoring, run_wizard


app = typer.Typer(
    name="hatch-being",
    help="Hatch a new agent-core being. Run with no flags for interactive TUI.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def hatch_being(
    config: Optional[Path] = typer.Option(
        None, "--config",
        help="Non-interactive: load HatchConfig from YAML.",
    ),
    vault_root: Optional[Path] = typer.Option(
        None, "--vault-root", "--root",
        help="Override the resolved vault root. Default: $HOME.",
    ),
    daemon_config_dir: Optional[Path] = typer.Option(
        None, "--daemon-config-dir",
        help="Override the daemon's config directory. Default: ~/.agent-core/.",
    ),
    init_missing: bool = typer.Option(
        False, "--init-missing",
        help="Top-up an existing vault with newly-added scaffolding files.",
    ),
) -> None:
    if config is not None:
        raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        cfg = HatchConfig(**raw)
        interactive = False
    else:
        cfg = run_wizard()
        interactive = True

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

    letter_authored = False
    if interactive:
        letter_authored = offer_letter_authoring(cfg)

    daemon_check_status = "skipped"  # Phase 5 keeps this simple; live daemon check is Phase 6 e2e.

    report_path = write_hatching_report(
        cfg, result,
        letter_authored=letter_authored,
        daemon_check_status=daemon_check_status,
    )

    typer.echo(f"\nHatched at {result.vault_root}")
    typer.echo(f"Report: {report_path}")
    typer.echo("🐣")
