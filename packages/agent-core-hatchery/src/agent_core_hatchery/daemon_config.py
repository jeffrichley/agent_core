"""Write daemon-config fragments for a hatched being.

Outputs (under <daemon_config_dir>):
- endpoints.d/<being>.yaml
- jobs.d/<being>.yaml

Phase 5 expands this with channels (Discord, webcam) and the optional
github_backup job.
"""

from __future__ import annotations

from pathlib import Path

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
        dest_dir = self._config.resolved_daemon_config_dir() / "endpoints.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(
                f"daemon endpoints fragment already exists: {dest.name} "
                f"(refusing to overwrite — mv aside or remove manually)"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        template = (self._templates_dir / "daemon-fragments" / "endpoints.yaml.j2").read_text(
            encoding="utf-8"
        )
        rendered = self._renderer.render_string(template)
        dest.write_text(rendered, encoding="utf-8")
        return dest

    def _write_jobs_fragment(self) -> Path:
        dest_dir = self._config.resolved_daemon_config_dir() / "jobs.d"
        dest = dest_dir / f"{self._config.being_name_lower}.yaml"
        if dest.exists():
            raise FileExistsError(
                f"daemon jobs fragment already exists: {dest.name}"
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        template = (self._templates_dir / "daemon-fragments" / "jobs.yaml.j2").read_text(
            encoding="utf-8"
        )
        rendered = self._renderer.render_string(template)
        dest.write_text(rendered, encoding="utf-8")
        return dest
