"""Release-artifact fetching + installation for the daemon.

Pure-ish functions: HTTP I/O is injected via a `fetcher` callable so unit
tests can stub it. The defaults use `urllib.request` (stdlib) so the
daemon venv carries no extra dependency.

Used by `agent-core daemon install --release` and `daemon refresh
--release` in `daemon/cli.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class NoReleasesError(Exception):
    """Raised when `latest` is requested but no releases exist for the repo."""


@dataclass(frozen=True)
class WheelAsset:
    """A `.whl` asset on a GitHub Release."""

    name: str
    download_url: str


# Default fetcher: urllib over the stdlib, returns raw bytes.
def _default_fetcher(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-core-daemon"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


Fetcher = Callable[[str], bytes]


def resolve_version(
    version: str | None,
    *,
    repo: str,
    fetcher: Fetcher | None = None,
) -> str:
    """Resolve a user-supplied version to a concrete `vX.Y.Z` tag.

    - `version="vX.Y.Z"` → returned unchanged.
    - `version=None` → query GitHub's `/releases/latest` endpoint, return its
      `tag_name`. Raises NoReleasesError if no releases exist.
    """
    if version is not None:
        return version

    if fetcher is None:
        fetcher = _default_fetcher
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        body = fetcher(url)
    except Exception as exc:
        raise NoReleasesError(
            f"could not resolve latest release for {repo} (no releases yet?): {exc}"
        ) from exc

    data = json.loads(body)
    return data["tag_name"]


def list_release_wheels(
    version: str,
    *,
    repo: str,
    fetcher: Fetcher | None = None,
) -> list[WheelAsset]:
    """Return the `.whl` assets attached to release `version` (e.g. `v0.1.0`)."""
    if fetcher is None:
        fetcher = _default_fetcher
    url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"
    body = fetcher(url)
    data = json.loads(body)
    wheels: list[WheelAsset] = []
    for asset in data.get("assets", []):
        if asset["name"].endswith(".whl"):
            wheels.append(
                WheelAsset(
                    name=asset["name"],
                    download_url=asset["browser_download_url"],
                )
            )
    return wheels


def download_wheels(
    assets: list[WheelAsset],
    *,
    dest: Path,
    fetcher: Fetcher | None = None,
) -> list[Path]:
    """Download each asset into `dest/`, skipping if a file of the same name exists.

    Skipping by name (not size/hash) is deliberate: the local cache at
    `~/.agent-core/releases/vX.Y.Z/` is keyed by exact release tag, so a
    matching filename implies a matching wheel. If you need to force a
    redownload, delete the cache dir.
    """
    if fetcher is None:
        fetcher = _default_fetcher
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for asset in assets:
        path = dest / asset.name
        if not path.exists():
            body = fetcher(asset.download_url)
            path.write_bytes(body)
        out.append(path)
    return out


def download_requirements(
    version: str,
    *,
    repo: str,
    dest: Path,
    fetcher: Fetcher | None = None,
) -> Path:
    """Download `requirements.txt` from the release `version` into `dest/`.

    Same skip-if-present cache logic as `download_wheels`. Returns the
    local path to the downloaded requirements file. Raises FileNotFoundError
    if no `requirements.txt` is attached to the release.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / "requirements.txt"
    if out_path.exists():
        return out_path

    if fetcher is None:
        fetcher = _default_fetcher
    url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"
    body = fetcher(url)
    data = json.loads(body)
    for asset in data.get("assets", []):
        if asset["name"] == "requirements.txt":
            out_path.write_bytes(fetcher(asset["browser_download_url"]))
            return out_path
    raise FileNotFoundError(
        f"release {version} has no requirements.txt asset; "
        f"cannot resolve dependencies (was the release built by Phase 2.5+?)"
    )


def ensure_venv(venv: Path, *, python_version: str = "3.12") -> None:
    """Create the daemon venv at `venv` if it doesn't already exist.

    Idempotent: no-op if `venv/Scripts/python.exe` (Windows) or
    `venv/bin/python` (POSIX) is present. Uses `uv venv` for cross-platform
    Python provisioning.
    """
    if sys.platform == "win32":
        existing = venv / "Scripts" / "python.exe"
    else:
        existing = venv / "bin" / "python"
    if existing.exists():
        return
    subprocess.run(
        ["uv", "venv", str(venv), "--python", python_version],
        check=True,
    )


def install_requirements(req_path: Path, *, venv_python: Path) -> None:
    """Install pinned dependencies from a requirements.txt into the daemon venv.

    Resolves the PyTorch cu130 index URL embedded in the requirements file.
    """
    cmd = [
        "uv", "pip", "install",
        "--python", str(venv_python),
        "--requirement", str(req_path),
    ]
    subprocess.run(cmd, check=True)


def install_wheels(wheel_paths: list[Path], *, venv_python: Path) -> None:
    """Replace the installed agent_core* packages in `venv_python`'s env with
    the contents of `wheel_paths`. Surgical — does not touch dependencies.

    Call AFTER `install_requirements` so deps are present.
    """
    cmd = [
        "uv", "pip", "install",
        "--python", str(venv_python),
        "--force-reinstall",
        "--no-deps",
        *[str(p) for p in wheel_paths],
    ]
    subprocess.run(cmd, check=True)
