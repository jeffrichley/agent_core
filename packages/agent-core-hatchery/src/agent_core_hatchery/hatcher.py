"""Hatcher orchestration: render templates → write vault → validate.

Phase 2 covers memory-template rendering only. Subsequent phases add:
- Phase 3: daemon-fragment writing + parse validation.
- Phase 4: elder-letter copying + skill rendering.
- Phase 5: TUI wizard, channel scaffolding, EDITOR gate, HATCHING-REPORT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_core_hatchery.config import HatchConfig
from agent_core_hatchery.file_classes import FileClassManifest
from agent_core_hatchery.renderer import Renderer
from agent_core_hatchery.validators import ValidationError, validate_load_bearing_paths


PACKAGE_ROOT = Path(__file__).parent.parent.parent
TEMPLATES_DIR = PACKAGE_ROOT / "templates"


class VaultExistsError(Exception):
    """Raised when default-mode hatch is attempted against an existing vault."""


@dataclass
class HatchResult:
    vault_root: Path
    files_written: list[Path] = field(default_factory=list)
    dirs_created: list[Path] = field(default_factory=list)
    files_added_in_topup: list[Path] = field(default_factory=list)


class Hatcher:
    def __init__(
        self,
        config: HatchConfig,
        templates_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._renderer = Renderer(config)
        self._manifest = FileClassManifest.load(self._templates_dir / "file-classes.yaml")
        self._tracked_writes: list[Path] = []

    def hatch(self) -> HatchResult:
        vault_root = self._config.resolved_vault_root()
        result = HatchResult(vault_root=vault_root)

        if vault_root.exists() and not self._config.init_missing:
            raise VaultExistsError(
                f"vault exists at {vault_root}\n"
                f"Either:\n"
                f"  - mv {vault_root} {vault_root}.bak.<date>/  and rerun\n"
                f"  - rerun with --init-missing for additive top-up"
            )

        try:
            self._render_memory_tree(result)

            # Phase 3: write daemon fragments + extended validation.
            # Skipped on init_missing: fragments are presumed already in place
            # from the original hatch; re-writing would raise FileExistsError
            # and the top-up flow is vault-only (not daemon-config).
            if not self._config.init_missing:
                from agent_core_hatchery.daemon_config import DaemonConfigWriter
                from agent_core_hatchery.validators import validate_daemon_fragments_parse

                daemon_writes = DaemonConfigWriter(self._config).write_all()
                self._tracked_writes.extend(daemon_writes)
                result.files_written.extend(daemon_writes)

            validate_load_bearing_paths(self._config)
            if not self._config.init_missing:
                validate_daemon_fragments_parse(self._config)
        except (ValidationError, Exception):
            self._rollback()
            raise

        return result

    def _render_memory_tree(self, result: HatchResult) -> None:
        memory_src = self._templates_dir / "memory"
        for src_path in sorted(memory_src.rglob("*")):
            rel = src_path.relative_to(memory_src)
            dest_rel = self._rewrite_being_dir(rel)
            dest_in_vault = self._config.resolved_vault_root() / "Memory" / dest_rel

            if src_path.is_dir():
                if not dest_in_vault.exists():
                    dest_in_vault.mkdir(parents=True, exist_ok=True)
                    self._tracked_writes.append(dest_in_vault)
                    result.dirs_created.append(dest_in_vault)
                continue

            # Strip the .j2 suffix from the destination if present
            dest = dest_in_vault.with_suffix("") if dest_in_vault.suffix == ".j2" else dest_in_vault

            if dest.exists() and self._config.init_missing:
                continue
            if dest.exists() and not self._config.init_missing:
                continue  # would have been caught by VaultExistsError; defensive

            dest.parent.mkdir(parents=True, exist_ok=True)
            content = src_path.read_text(encoding="utf-8")
            if src_path.suffix == ".j2":
                content = self._renderer.render_string(content)
            dest.write_text(content, encoding="utf-8")
            self._tracked_writes.append(dest)
            if self._config.init_missing:
                result.files_added_in_topup.append(dest)
            else:
                result.files_written.append(dest)

    def _rewrite_being_dir(self, rel: Path) -> Path:
        """Rename the placeholder `_being_/` segment to <being_name_lower>/."""
        parts = list(rel.parts)
        if "_being_" in parts:
            parts = [
                self._config.being_name_lower if p == "_being_" else p
                for p in parts
            ]
        return Path(*parts) if parts else rel

    def _rollback(self) -> None:
        for path in reversed(self._tracked_writes):
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
