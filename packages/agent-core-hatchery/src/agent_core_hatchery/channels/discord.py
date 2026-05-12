"""Discord channel scaffolding."""

from __future__ import annotations

import json
import os
from typing import Any

from agent_core_hatchery.config import HatchConfig


def scaffold_discord(config: HatchConfig) -> dict[str, Any] | None:
    if not config.channels.discord.enabled:
        return None

    daemon_dir = config.resolved_daemon_config_dir()
    daemon_dir.mkdir(parents=True, exist_ok=True)
    env_file = daemon_dir / f"discord-{config.being_name_lower}.env"
    env_file.write_text(
        f"{config.discord_token_env}={config.channels.discord.resolved_token}\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        env_file.chmod(0o600)

    block: dict[str, Any] = {
        "type": "builtin.discord",
        "name": f"discord-{config.being_name_lower}",
        "description": f"Discord adapter for {config.being_name}.",
        "params": {
            "target": config.endpoint_name,
            "token_env": config.discord_token_env,
            "env_file": str(env_file),
        },
    }
    if config.channels.discord.channel_allowlist:
        access_file = daemon_dir / f"discord-{config.being_name_lower}-access.json"
        access_file.write_text(
            json.dumps({"channels": config.channels.discord.channel_allowlist}, indent=2),
            encoding="utf-8",
        )
        block["params"]["access_config_path"] = str(access_file)
    return block
