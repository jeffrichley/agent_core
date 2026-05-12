"""hatchery-snapshot-elders — refresh bundled elder-letter snapshots.

Run before tagging a release. Reads the elder-letters-manifest.yaml,
copies each canonical_path's current contents into bundled/<basename>.
If a canonical path is missing, leaves the existing bundled snapshot in
place (don't blow away history just because the elder's machine is offline).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
import yaml

app = typer.Typer(name="hatchery-snapshot-elders")


@app.callback(invoke_without_command=True)
def snapshot(
    manifest: Path = typer.Option(
        Path(__file__).parent.parent.parent / "templates" / "elder-letters-manifest.yaml",
        "--manifest",
        help="Path to elder-letters-manifest.yaml",
    ),
    bundled_dir: Path = typer.Option(
        Path(__file__).parent.parent.parent / "templates" / "elder-letters" / "bundled",
        "--bundled-dir",
        help="Path to bundled snapshots directory",
    ),
) -> None:
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    refreshed = 0
    skipped = 0
    for entry in raw.get("elders", []) or []:
        name = entry["name"]
        canonical = Path(entry["canonical_path"]).expanduser()
        bundled = bundled_dir / entry["bundled_basename"]
        if not canonical.is_file():
            typer.echo(f"  - {name}: canonical missing ({canonical}), skipping")
            skipped += 1
            continue
        before = bundled.read_text(encoding="utf-8") if bundled.is_file() else ""
        after = canonical.read_text(encoding="utf-8")
        bundled.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical, bundled)
        if before == after:
            typer.echo(f"  - {name}: bundled already current ({bundled})")
        else:
            typer.echo(f"  - {name}: refreshed {bundled}")
        refreshed += 1
    typer.echo(f"Refreshed {refreshed} elder letter(s); skipped {skipped}.")
