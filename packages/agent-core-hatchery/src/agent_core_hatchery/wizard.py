"""Questionary TUI wizard.

Asks the human for inputs, returns a HatchConfig. Designed so the
prompt boundary is testable in isolation (validators are pure functions);
the prompt orchestration is called only via `run_wizard()`.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import questionary
import typer

from agent_core_hatchery.config import (
    ChannelsConfig,
    DiscordChannelConfig,
    GitHubBackupConfig,
    HatchConfig,
    WebcamChannelConfig,
)


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_KEBAB_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def _validate_being_name(value: str) -> bool | str:
    if not value.strip():
        return "Being's name cannot be empty"
    if not _NAME_RE.match(value):
        return "Use letters, digits, hyphen, underscore; start with a letter"
    return True


def _validate_endpoint_name(value: str) -> bool | str:
    if not _KEBAB_RE.match(value):
        return "Endpoint name must be kebab-case-friendly (lowercase, no spaces)"
    return True


def _validate_path_writable(value: str) -> bool | str:
    p = Path(value).expanduser()
    if not p.exists():
        return f"Path does not exist: {p}"
    if not p.is_dir():
        return f"Path is not a directory: {p}"
    if not os.access(p, os.W_OK):
        return f"Path not writable: {p}"
    return True


def run_wizard() -> HatchConfig:
    typer.secho("\n  ── agent-core hatchery ──", bold=True)
    typer.echo("  Hatching a new being. Inputs first, confirmations second, then birth.\n")

    typer.secho("  ── Identity ──", bold=True)
    being_name = questionary.text("Being's name:", validate=_validate_being_name).ask()
    if being_name is None:
        raise typer.Abort()
    being_emoji = questionary.text(
        "Emoji (or leave blank — your call to define deliberately):", default=""
    ).ask() or ""
    primary_human_name = questionary.text("Primary human's name:").ask()
    if not primary_human_name:
        raise typer.Abort()
    role = questionary.text("Short role placeholder (or skip):", default="").ask() or None

    typer.secho("\n  ── Substrate ──", bold=True)
    vault_root = questionary.text(
        "Vault root directory:",
        default=str(Path.home()),
        validate=_validate_path_writable,
    ).ask()
    being_lower = being_name.lower()
    resolved_vault = Path(vault_root).expanduser().resolve() / f".{being_lower}"
    typer.echo(f"  Resolved vault path: {resolved_vault}")
    if resolved_vault.exists():
        typer.secho(
            f"  ⚠ Vault already exists. mv it aside or use --init-missing.",
            fg=typer.colors.YELLOW,
        )

    endpoint_name = questionary.text(
        "agent-core endpoint name:",
        default=being_lower,
        validate=_validate_endpoint_name,
    ).ask()

    typer.secho("\n  ── Optional channels & integrations ──", bold=True)

    discord_cfg = DiscordChannelConfig()
    if questionary.confirm(f"Install Discord channel for {being_name}?", default=False).ask():
        token = questionary.password("Discord bot token (input hidden):").ask() or ""
        allowlist_raw = questionary.text(
            "Channel allowlist (comma-separated channel IDs — Discord developer mode "
            "shows these on right-click → Copy Channel ID; blank = all):",
            default="",
        ).ask() or ""
        discord_cfg = DiscordChannelConfig(
            enabled=True,
            token=token,
            channel_allowlist=[s.strip() for s in allowlist_raw.split(",") if s.strip()],
        )

    webcam_cfg = WebcamChannelConfig()
    if questionary.confirm("Install webcam channel?", default=False).ask():
        webcam_cfg = WebcamChannelConfig(enabled=True)

    github_cfg = GitHubBackupConfig()
    if questionary.confirm(f"Install GitHub backup of Memory/?", default=False).ask():
        repo_url = questionary.text(
            "Repo URL (or 'gh' to assume configured gh CLI):",
        ).ask() or ""
        github_cfg = GitHubBackupConfig(enabled=True, repo_url=repo_url)

    cfg = HatchConfig(
        being_name=being_name,
        being_emoji=being_emoji,
        primary_human_name=primary_human_name,
        being_role_placeholder=role,
        endpoint_name=endpoint_name,
        vault_root=str(vault_root),
        channels=ChannelsConfig(
            discord=discord_cfg, webcam=webcam_cfg, github_backup=github_cfg
        ),
    )

    typer.secho("\n  ── Universal scaffolding (always installed) ──", bold=True)
    typer.echo("   • Memory templates")
    typer.echo("   • 3 universal skills (skill-author, vault-lint, spawning-subagents)")
    typer.echo(
        "   • 5 always-on scheduler jobs (heartbeat, nightly_reflection, vault_lint, "
        "auth_health_probe, service_liveness_probe)"
        + (" + 1 opt-in (github_backup, included)" if github_cfg.enabled else "")
    )
    typer.echo("   • Elder letters: resolved at hatch time from manifest")

    typer.secho(f"\n  ── Preview ──", bold=True)
    typer.echo(f"  Will create: {resolved_vault}")
    typer.echo(
        f"  Will modify: ~/.agent-core/endpoints.d/{being_lower}.yaml"
        f" + jobs.d/{being_lower}.yaml"
    )
    if discord_cfg.enabled:
        typer.echo(f"               ~/.agent-core/discord-{being_lower}.env")

    if not questionary.confirm("Confirm hatching?", default=True).ask():
        raise typer.Abort()

    return cfg


def offer_letter_authoring(config: HatchConfig) -> bool:
    """Post-hatch prompt to author from-her-creator.md in $EDITOR.

    Returns True if the human authored the letter (file is non-template).
    """
    if not config.author_letter_in_editor:
        return False

    letter_path = (
        config.resolved_vault_root()
        / "Memory"
        / config.being_name_lower
        / "letters"
        / "from-her-creator.md"
    )
    if not letter_path.is_file():
        return False

    if not questionary.confirm(
        f"Open editor on from-her-creator.md now?", default=True
    ).ask():
        return False

    template_text = letter_path.read_text(encoding="utf-8")
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    try:
        subprocess.run([editor, str(letter_path)], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        typer.secho(f"  Editor exited with error: {exc}", fg=typer.colors.YELLOW)
        return False

    after = letter_path.read_text(encoding="utf-8")
    return after != template_text and after.strip() != ""
