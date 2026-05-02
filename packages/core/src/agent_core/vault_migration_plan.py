"""Dry-run helpers for Pepper / agent vault cutover (cutover #06).

Walks the on-disk Memory vault, checks for recommended layout markers, and
collects absolute paths embedded in YAML configs (for example ``base_path`` in
``agent_core.yaml``). Nothing writes outside an optional ``--json-out`` report.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

vault_app = typer.Typer(
    name="vault",
    help="Vault continuity helpers (Pepper cutover #06).",
    no_args_is_help=True,
)

RECOMMENDED_ROOT_FILES = (
    "IDENTITY.md",
    "SOUL.md",
    "USER.md",
    "MEMORY.md",
    "OPERATIONS.md",
    "TASKS.md",
)
RECOMMENDED_DIRS = ("projects", "daily", "ideas", "playbooks", "pepper")


def iter_string_values(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from iter_string_values(v)
    elif isinstance(node, list):
        for item in node:
            yield from iter_string_values(item)


def is_probable_absolute_path(s: str) -> bool:
    t = s.strip().strip("'\"")
    if len(t) < 3:
        return False
    if t[0].isalpha() and t[1] == ":" and t[2] in "\\/":
        return True
    if t.startswith("\\\\"):
        return True
    if t.startswith("/") and len(t) >= 2 and t[1] != "/":
        return True
    return False


def absolute_paths_in_yaml(text: str) -> list[str]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for s in iter_string_values(data):
        if not isinstance(s, str) or not is_probable_absolute_path(s):
            continue
        t = s.strip()
        if t not in seen:
            seen.add(t)
            out.append(t)
    return sorted(out)


def scan_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    return {"config": str(path.resolve()), "absolute_paths": absolute_paths_in_yaml(text)}


def vault_layout_report(vault: Path) -> dict[str, Any]:
    root = vault.resolve()
    missing_files = [n for n in RECOMMENDED_ROOT_FILES if not (root / n).is_file()]
    missing_dirs = [n for n in RECOMMENDED_DIRS if not (root / n).is_dir()]
    files: list[dict[str, Any]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                rel = Path(p).as_posix()
            files.append({"path": rel, "size_bytes": p.stat().st_size})
    summaries = root / "daily" / "summaries"
    summaries_note: str | None = None
    if summaries.is_dir():
        n = sum(1 for _ in summaries.iterdir())
        summaries_note = f"daily/summaries exists ({n} top-level entries)"
    elif (root / "daily").is_dir():
        summaries_note = "daily/ exists but daily/summaries/ missing (optional per cutover #06)"
    return {
        "vault": str(root),
        "file_count": len(files),
        "files": files,
        "missing_recommended_files": missing_files,
        "missing_recommended_dirs": missing_dirs,
        "daily_summaries": summaries_note,
    }


def build_plan(vault: Path, config_files: list[Path]) -> dict[str, Any]:
    layout = vault_layout_report(vault)
    cfg_scans = [scan_config_file(p.resolve()) for p in config_files]
    return {"vault_layout": layout, "configs": cfg_scans}


@vault_app.command("plan-dry-run")
def plan_dry_run_cmd(
    vault: Annotated[
        Path,
        typer.Option(
            "--vault",
            exists=True,
            file_okay=False,
            help="Memory vault root (e.g. …/.pepper/Memory).",
        ),
    ],
    config: Annotated[
        list[Path] | None,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            help="YAML file to scan for absolute paths (repeatable).",
        ),
    ] = None,
    json_out: Annotated[
        Path | None,
        typer.Option("--json-out", help="Write the same JSON payload to this path."),
    ] = None,
) -> None:
    """Print a JSON dry-run report; no vault files are modified."""
    plan = build_plan(vault, list(config or []))
    payload = json.dumps(plan, indent=2)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(payload, encoding="utf-8")
    typer.echo(payload)
