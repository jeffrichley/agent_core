"""HatchConfig — pydantic model for the hatcher's input contract.

Both the TUI wizard and `--config <yaml>` mode produce instances of this
model. The model is the single source of truth for what inputs the
hatcher needs.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _substitute_env_vars(value: str) -> str:
    """Replace ${VAR} occurrences with os.environ[VAR]. Raises ValueError if missing."""

    def repl(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in os.environ:
            raise ValueError(
                f"Environment variable {var_name!r} referenced in config but not set"
            )
        return os.environ[var_name]

    return _ENV_VAR_PATTERN.sub(repl, value)


class DiscordChannelConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    channel_allowlist: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @property
    def resolved_token(self) -> str:
        return _substitute_env_vars(self.token)


class WebcamChannelConfig(BaseModel):
    enabled: bool = False

    model_config = ConfigDict(extra="forbid")


class GitHubBackupConfig(BaseModel):
    enabled: bool = False
    repo_url: str = ""

    model_config = ConfigDict(extra="forbid")


class ChannelsConfig(BaseModel):
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    webcam: WebcamChannelConfig = Field(default_factory=WebcamChannelConfig)
    github_backup: GitHubBackupConfig = Field(default_factory=GitHubBackupConfig)

    model_config = ConfigDict(extra="forbid")


class HatchConfig(BaseModel):
    being_name: str
    being_emoji: str = ""
    primary_human_name: str
    being_role_placeholder: str | None = None
    # endpoint_name is Optional[str]; after validation, None is replaced by being_name_lower.
    # This allows `endpoint_name: foo` in YAML to override, while defaulting to being_name_lower.
    endpoint_name: str | None = None
    vault_root: str | None = None
    daemon_config_dir: str = "~/.agent-core"
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    init_missing: bool = False
    author_letter_in_editor: bool = True

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _resolve_defaults(self) -> HatchConfig:
        if self.endpoint_name is None:
            self.endpoint_name = self.being_name.lower()
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def being_name_lower(self) -> str:
        return self.being_name.lower()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def being_name_upper(self) -> str:
        return self.being_name.upper()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hatched_date(self) -> str:
        return date.today().isoformat()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def discord_token_env(self) -> str:
        return f"DISCORD_{self.being_name_upper}_TOKEN"

    def resolved_vault_root(self) -> Path:
        root = Path(self.vault_root or "~").expanduser().resolve()
        return (root / f".{self.being_name_lower}").resolve()

    def resolved_vault_memory_path(self) -> Path:
        return self.resolved_vault_root() / "Memory"

    def resolved_daemon_config_dir(self) -> Path:
        return Path(self.daemon_config_dir).expanduser().resolve()
