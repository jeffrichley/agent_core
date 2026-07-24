"""hatch-being CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.daemon_probe import reload_and_probe
from agent_core_hatchery.hatcher import Hatcher, VaultExistsError
from agent_core_hatchery.report import DaemonCheckStatus, write_hatching_report
from agent_core_hatchery.wizard import offer_letter_authoring, run_wizard

app = typer.Typer(
    name="hatch-being",
    help="Hatch a new agent-core being. Run with no flags for interactive TUI.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def hatch_being(
    config: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        help="Non-interactive: load HatchConfig from YAML.",
    ),
    vault_root: Path | None = typer.Option(  # noqa: B008
        None,
        "--vault-root",
        "--root",
        help="Override the resolved vault root. Default: $HOME.",
    ),
    daemon_config_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--daemon-config-dir",
        help="Override the daemon's config directory. Default: ~/.agent-core/.",
    ),
    init_missing: bool = typer.Option(
        False,
        "--init-missing",
        help="Top-up an existing vault with newly-added scaffolding files.",
    ),
    no_daemon_reload: bool = typer.Option(
        False,
        "--no-daemon-reload",
        help="Skip the post-hatch daemon reload+probe. Use when hatching without "
        "a running installed daemon — CI, containers, or an air-gapped first "
        "hatch. The being is still fully hatched; only the live handoff is skipped.",
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
        raise typer.Exit(code=1) from None

    letter_authored = False
    if interactive:
        letter_authored = offer_letter_authoring(cfg)

    daemon_check_status: DaemonCheckStatus
    if cfg.init_missing or no_daemon_reload:
        daemon_check_status = "skipped"
    else:
        daemon_check_status = reload_and_probe(cfg)

    report_path = write_hatching_report(
        cfg,
        result,
        letter_authored=letter_authored,
        daemon_check_status=daemon_check_status,
    )

    typer.echo(f"\nHatched at {result.vault_root}")
    typer.echo(f"Report: {report_path}")
    typer.echo("🐣")

    if daemon_check_status == "start_failed":
        typer.secho(
            "✗  Daemon FAILED to restart after hatch — the fleet may be OFFLINE. "
            "Run `agent-core daemon start` now.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if daemon_check_status == "reachable_but_missing":
        typer.secho(
            "⚠  Daemon probe: endpoint not registered after reload. See HATCHING-REPORT.md.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
