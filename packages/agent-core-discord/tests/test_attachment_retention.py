"""Retention sweep for auto-downloaded attachments (#76)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_core_discord.endpoint import DiscordEndpoint


def _ep(tmp_path: Path, **kw) -> DiscordEndpoint:
    return DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
        **kw,
    )


def _mkenv(root: Path, env_id: str, *, nbytes: int, age_seconds: float) -> Path:
    d = root / env_id
    d.mkdir(parents=True, exist_ok=True)
    f = d / "file.bin"
    f.write_bytes(b"x" * nbytes)
    old = time.time() - age_seconds
    os.utime(d, (old, old))
    os.utime(f, (old, old))
    return d


def test_sweep_evicts_dirs_older_than_retention(tmp_path):
    ep = _ep(tmp_path, attachment_retention_days=1)
    fresh = _mkenv(tmp_path, "fresh", nbytes=10, age_seconds=0)
    stale = _mkenv(tmp_path, "stale", nbytes=10, age_seconds=2 * 86400)
    ep._sweep_attachments_once()
    assert fresh.exists()
    assert not stale.exists()


def test_sweep_enforces_size_cap_oldest_first(tmp_path):
    ep = _ep(tmp_path, attachment_retention_days=3650, attachment_max_total_bytes=250)
    old = _mkenv(tmp_path, "old", nbytes=200, age_seconds=3000)
    mid = _mkenv(tmp_path, "mid", nbytes=200, age_seconds=2000)
    new = _mkenv(tmp_path, "new", nbytes=200, age_seconds=1000)
    ep._sweep_attachments_once()
    assert not old.exists()
    assert not mid.exists()
    assert new.exists()


def test_sweep_skips_failed_delete_and_does_not_crash(tmp_path, monkeypatch):
    ep = _ep(tmp_path, attachment_retention_days=1)
    _mkenv(tmp_path, "stale", nbytes=10, age_seconds=2 * 86400)

    import shutil

    def boom(path):
        raise PermissionError("locked")

    monkeypatch.setattr(shutil, "rmtree", boom)
    # Must not raise even though every delete fails.
    ep._sweep_attachments_once()


def test_sweep_noop_when_dir_missing(tmp_path):
    ep = _ep(tmp_path / "does-not-exist", attachment_retention_days=1)
    ep._sweep_attachments_once()  # must not raise


@pytest.mark.asyncio
async def test_attachment_sweep_task_cancels_cleanly(tmp_path):
    import asyncio

    ep = _ep(tmp_path, attachment_sweep_seconds=0.01, attachment_retention_days=1)
    # Construct the loop task directly to assert lifecycle semantics without
    # standing up a real Discord client.
    ep._attachment_sweep_task = asyncio.create_task(ep._attachment_sweep_loop())
    await asyncio.sleep(0.03)
    ep._attachment_sweep_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ep._attachment_sweep_task
