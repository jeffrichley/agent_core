"""Unit tests for daemon/release.py — pure functions over a fake fetcher."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from agent_core.daemon.release import (
    NoReleasesError,
    WheelAsset,
    download_requirements,
    download_wheels,
    ensure_venv,
    list_release_wheels,
    resolve_version,
)

# ---- resolve_version --------------------------------------------------------

def _fake_fetcher(responses: dict[str, bytes]) -> Callable[[str], bytes]:
    def f(url: str) -> bytes:
        if url not in responses:
            raise RuntimeError(f"unexpected URL: {url}")
        return responses[url]
    return f


def test_resolve_version_explicit_passes_through() -> None:
    # Explicit version — fetcher must not be called.
    f = _fake_fetcher({})  # any call would raise
    assert resolve_version("v0.1.0", repo="x/y", fetcher=f) == "v0.1.0"


def test_resolve_version_latest_resolves_from_api() -> None:
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/latest": json.dumps(
            {"tag_name": "v0.2.0"}
        ).encode("utf-8")
    })
    assert resolve_version(None, repo="x/y", fetcher=f) == "v0.2.0"


def test_resolve_version_no_releases_raises() -> None:
    # GitHub API returns 404 → fetcher raises; we wrap it.
    def f(url: str) -> bytes:
        raise RuntimeError("404 Not Found")

    with pytest.raises(NoReleasesError):
        resolve_version(None, repo="x/y", fetcher=f)


# ---- list_release_wheels ----------------------------------------------------

_RELEASE_JSON = json.dumps({
    "tag_name": "v0.1.0",
    "assets": [
        {"name": "agent_core-0.1.0-py3-none-any.whl",
         "browser_download_url": "https://example/agent_core-0.1.0-py3-none-any.whl"},
        {"name": "agent_core_busproxy-0.1.0-py3-none-any.whl",
         "browser_download_url": "https://example/agent_core_busproxy-0.1.0-py3-none-any.whl"},
        {"name": "checksums.txt",
         "browser_download_url": "https://example/checksums.txt"},
        {"name": "Source code (zip)",
         "browser_download_url": "https://example/source.zip"},
    ],
}).encode("utf-8")


def test_list_release_wheels_filters_to_whl_only() -> None:
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/tags/v0.1.0": _RELEASE_JSON
    })
    wheels = list_release_wheels("v0.1.0", repo="x/y", fetcher=f)
    assert [w.name for w in wheels] == [
        "agent_core-0.1.0-py3-none-any.whl",
        "agent_core_busproxy-0.1.0-py3-none-any.whl",
    ]
    assert all(isinstance(w, WheelAsset) for w in wheels)


# ---- download_wheels --------------------------------------------------------

def test_download_wheels_writes_files(tmp_path: Path) -> None:
    assets = [
        WheelAsset(
            name="a.whl",
            download_url="https://example/a.whl",
        ),
        WheelAsset(
            name="b.whl",
            download_url="https://example/b.whl",
        ),
    ]
    f = _fake_fetcher({
        "https://example/a.whl": b"WHEEL_BYTES_A",
        "https://example/b.whl": b"WHEEL_BYTES_B",
    })

    paths = download_wheels(assets, dest=tmp_path, fetcher=f)

    assert sorted(p.name for p in paths) == ["a.whl", "b.whl"]
    assert (tmp_path / "a.whl").read_bytes() == b"WHEEL_BYTES_A"
    assert (tmp_path / "b.whl").read_bytes() == b"WHEEL_BYTES_B"


def test_download_wheels_skips_existing_with_same_size(tmp_path: Path) -> None:
    # Pre-populate one wheel.
    (tmp_path / "a.whl").write_bytes(b"WHEEL_BYTES_A")

    assets = [WheelAsset(name="a.whl", download_url="https://example/a.whl")]

    call_count = {"n": 0}

    def f(url: str) -> bytes:
        call_count["n"] += 1
        return b"WHEEL_BYTES_A"

    download_wheels(assets, dest=tmp_path, fetcher=f)
    assert call_count["n"] == 0, "fetcher should not be called for an already-present file"


# ---- download_requirements --------------------------------------------------

_RELEASE_JSON_WITH_REQS = json.dumps({
    "tag_name": "v0.1.0",
    "assets": [
        {"name": "agent_core-0.1.0-py3-none-any.whl",
         "browser_download_url": "https://example/agent_core-0.1.0-py3-none-any.whl"},
        {"name": "requirements.txt",
         "browser_download_url": "https://example/requirements.txt"},
    ],
}).encode("utf-8")


def test_download_requirements_writes_file(tmp_path: Path) -> None:
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/tags/v0.1.0": _RELEASE_JSON_WITH_REQS,
        "https://example/requirements.txt": b"# pinned deps\ntorch==2.12.0+cu130\n",
    })
    path = download_requirements("v0.1.0", repo="x/y", dest=tmp_path, fetcher=f)
    assert path == tmp_path / "requirements.txt"
    assert (tmp_path / "requirements.txt").read_text() == "# pinned deps\ntorch==2.12.0+cu130\n"


def test_download_requirements_missing_raises(tmp_path: Path) -> None:
    # Release exists but has no requirements.txt attached
    release_json = json.dumps({
        "tag_name": "v0.0.9",
        "assets": [
            {"name": "agent_core-0.0.9-py3-none-any.whl",
             "browser_download_url": "https://example/x.whl"},
        ],
    }).encode("utf-8")
    f = _fake_fetcher({
        "https://api.github.com/repos/x/y/releases/tags/v0.0.9": release_json,
    })
    with pytest.raises(FileNotFoundError, match="requirements.txt"):
        download_requirements("v0.0.9", repo="x/y", dest=tmp_path, fetcher=f)


def test_download_requirements_skips_existing(tmp_path: Path) -> None:
    # Pre-populate
    (tmp_path / "requirements.txt").write_text("cached content")

    calls = {"n": 0}

    def f(url: str) -> bytes:
        calls["n"] += 1
        return b"new content"

    path = download_requirements("v0.1.0", repo="x/y", dest=tmp_path, fetcher=f)
    assert calls["n"] == 0
    assert path.read_text() == "cached content"


# ---- ensure_venv ------------------------------------------------------------

def test_ensure_venv_no_op_when_python_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    # Pre-create the venv python
    if sys.platform == "win32":
        py = tmp_path / "Scripts" / "python.exe"
    else:
        py = tmp_path / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_run)

    ensure_venv(tmp_path)
    assert calls == [], "uv venv should not be invoked when python already exists"


def test_ensure_venv_creates_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venv = tmp_path / "newvenv"
    # venv dir doesn't exist yet
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_run)

    ensure_venv(venv, python_version="3.12")
    assert len(calls) == 1
    assert calls[0][:3] == ["uv", "venv", str(venv)]
    assert "--python" in calls[0]
    assert "3.12" in calls[0]


# ---- keystone enforcer: install code path identity --------------------------

def test_install_code_path_identity_between_prod_and_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """KEYSTONE ENFORCER (Phase 3.5 spec).

    Proves that `daemon install --instance test --release vX.Y.Z` invokes the
    SAME release.py functions with the SAME structural arguments as
    `daemon install --instance prod --release vX.Y.Z`, modulo the home path.

    Any future change that adds a function call to one path but not the other,
    or passes a structurally different argument shape, fails this test
    immediately. The keystone is enforced by code-equality, not docs.
    """
    import json as _json

    # release.py functions actually called by the install command (7 total).
    # write_stamp lives in install.py and is captured separately.
    release_fn_names = [
        "resolve_version",
        "list_release_wheels",
        "download_wheels",
        "download_requirements",
        "ensure_venv",
        "install_requirements",
        "install_wheels",
    ]

    calls: dict[str, list[tuple]] = {"prod": [], "test": []}

    fake_release_json = _json.dumps({
        "tag_name": "v0.2.0",
        "assets": [
            {"name": "agent_core-0.2.0-py3-none-any.whl",
             "browser_download_url": "https://example/agent_core-0.2.0.whl"},
            {"name": "requirements.txt",
             "browser_download_url": "https://example/requirements.txt"},
        ],
    }).encode("utf-8")

    def fake_fetcher(url: str) -> bytes:
        if url.endswith("/releases/tags/v0.2.0"):
            return fake_release_json
        if url.endswith(".whl"):
            return b"FAKE_WHEEL"
        if url.endswith("/requirements.txt"):
            return b"# pinned\ntorch==2.12.0\n"
        raise RuntimeError(f"unexpected URL: {url}")

    def fake_subprocess_run(cmd, **kwargs):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    def make_release_capture(fn_name: str, label: str, original: object):
        """Wrap a release.py function to record its positional + keyword args."""
        def _wrapper(*args, **kwargs):
            calls[label].append((fn_name, args, kwargs))
            return original(*args, **kwargs)  # type: ignore[operator]

        return _wrapper

    def patch_release_for(label: str, originals: dict[str, object]) -> None:
        # cli.py uses `from agent_core.daemon.release import <fn>` — direct
        # name bindings.  We must patch cli.py's namespace, not release.py's,
        # to intercept what the install command actually calls.
        import agent_core.daemon.cli as _cli
        for fn in release_fn_names:
            monkeypatch.setattr(_cli, fn, make_release_capture(fn, label, originals[fn]))

    def normalize(
        captured: list[tuple], home: Path
    ) -> list[tuple]:
        """Replace the home-path string with a placeholder so prod vs test can
        be compared structurally independent of which home directory was used."""
        home_str = str(home)

        def norm(v: object) -> object:
            if isinstance(v, Path):
                s = str(v)
                return s.replace(home_str, "<HOME>") if home_str in s else s
            if isinstance(v, str):
                return v.replace(home_str, "<HOME>") if home_str in v else v
            if isinstance(v, list):
                return [norm(x) for x in v]
            if isinstance(v, tuple):
                return tuple(norm(x) for x in v)
            if isinstance(v, dict):
                return {k: norm(val) for k, val in v.items()}
            return v

        return [(name, norm(args), norm(kwargs)) for name, args, kwargs in captured]

    def invoke_install(instance: str) -> None:
        from typer.testing import CliRunner

        from agent_core.daemon.cli import app as daemon_app
        result = CliRunner().invoke(
            daemon_app, ["install", "--instance", instance, "--release", "v0.2.0"]
        )
        assert result.exit_code == 0, (
            f"install --instance {instance} exited {result.exit_code}:\n{result.output}"
        )

    # Patch network + subprocess (internals of release.py — these are called
    # from within the release functions, so patching the release module's
    # namespace is correct here).
    monkeypatch.setattr("agent_core.daemon.release._default_fetcher", fake_fetcher)
    monkeypatch.setattr("agent_core.daemon.release.subprocess.run", fake_subprocess_run)

    # Snapshot TRUE originals from cli.py's namespace (where they were imported
    # via `from agent_core.daemon.release import ...`).  We must patch cli.py's
    # bindings — not release.py's — to intercept what the install command calls.
    import agent_core.daemon.cli as _cli
    true_originals: dict[str, object] = {
        fn: getattr(_cli, fn) for fn in release_fn_names
    }

    # --- Run 1: prod ---
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path / "prod"))
    patch_release_for("prod", true_originals)
    invoke_install("prod")

    # Restore cli.py's bindings to true originals between runs so run 2's
    # wrappers don't chain through run 1's wrappers.
    for fn in release_fn_names:
        monkeypatch.setattr(_cli, fn, true_originals[fn])

    # --- Run 2: test ---
    monkeypatch.setenv("AGENT_CORE_HOME", str(tmp_path / "test"))
    patch_release_for("test", true_originals)
    invoke_install("test")

    # --- Assert structural identity modulo home path ---
    prod_norm = normalize(calls["prod"], tmp_path / "prod")
    test_norm = normalize(calls["test"], tmp_path / "test")

    assert prod_norm == test_norm, (
        "KEYSTONE BROKEN — install code path diverged between prod and test.\n"
        f"PROD calls ({len(prod_norm)}):\n"
        + "\n".join(f"  {c}" for c in prod_norm)
        + f"\nTEST calls ({len(test_norm)}):\n"
        + "\n".join(f"  {c}" for c in test_norm)
    )
