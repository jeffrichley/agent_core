"""Write daemon-config fragments for a hatched being.

Outputs (under <daemon_config_dir>):
- endpoints.d/<being>.yaml — claude_code_mcp + briefs.<being> always; +
  discord-<being> and webcam-<being> if those channels were enabled.
- jobs.d/<being>.yaml — 5 always-on scheduler jobs prefixed with being
  name; + <being>-github_backup if GitHub backup was enabled.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.hatcher import TEMPLATES_DIR
from agent_core_hatchery.renderer import Renderer


class DaemonConfigWriter:
    def __init__(self, config: HatchConfig, templates_dir: Path | None = None) -> None:
        self._config = config
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._renderer = Renderer(config)

    def write_all(self) -> list[Path]:
        written: list[Path] = []
        written.append(self._write_endpoints_fragment())
        written.append(self._write_jobs_fragment())
        return written

    def _write_endpoints_fragment(self) -> Path:
        from agent_core_hatchery.channels.discord import scaffold_discord
        from agent_core_hatchery.channels.webcam import scaffold_webcam

        always_on_template = (self._templates_dir / "daemon-fragments" / "endpoints.yaml.j2").read_text(
            encoding="utf-8"
        )
        always_on_yaml = self._renderer.render_string(always_on_template)
        merged: dict = yaml.safe_load(always_on_yaml) or {}
        merged.setdefault("endpoints", [])

        for scaffold in (scaffold_discord, scaffold_webcam):
            block = scaffold(self._config)
            if block is not None:
                merged["endpoints"].append(block)

        dest_dir = self._config.resolved_daemon_config_dir() / "endpoints.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(
                f"daemon endpoints fragment already exists: {dest.name} "
                f"(refusing to overwrite — mv aside or remove manually)"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
        return dest

    def _write_jobs_fragment(self) -> Path:
        from agent_core_hatchery.channels.github_backup import scaffold_github_backup

        always_on_template = (self._templates_dir / "daemon-fragments" / "jobs.yaml.j2").read_text(
            encoding="utf-8"
        )
        always_on_yaml = self._renderer.render_string(always_on_template)
        merged: dict = yaml.safe_load(always_on_yaml) or {}

        github_job = scaffold_github_backup(self._config)
        if github_job:
            for k, v in github_job.items():
                merged[k] = v

        dest_dir = self._config.resolved_daemon_config_dir() / "jobs.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(
                f"daemon jobs fragment already exists: {dest.name}"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
        return dest
