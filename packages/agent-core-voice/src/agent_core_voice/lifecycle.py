"""File lifecycle for voice WAVs: content-addressed write + TTL cleanup."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def write_addressed(
    audio: bytes,
    *,
    root: Path,
    retain_s: float,
) -> tuple[Path, str]:
    """Write ``audio`` to ``<root>/<sha256>.wav`` with a meta sidecar.

    Returns (path, sha256_hex). The WAV body is content-addressed and
    only written if not already present (so identical audio dedupes).
    The meta sidecar IS rewritten on every call, refreshing
    ``written_at_utc`` and effectively resetting the TTL window —
    LRU-ish semantics: actively-touched WAVs stay alive longer.
    """
    sha = hashlib.sha256(audio).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    wav_path = root / f"{sha}.wav"
    meta_path = wav_path.with_suffix(".meta.json")

    if not wav_path.exists():
        wav_path.write_bytes(audio)

    meta = {
        "sha256": sha,
        "retain_s": retain_s,
        "written_at_utc": datetime.now(UTC).isoformat(),
    }
    meta_path.write_text(json.dumps(meta))
    return wav_path, sha


def retain_until_iso(*, retain_s: float, now: datetime | None = None) -> str:
    """Compute ISO 8601 UTC timestamp of ``now + retain_s``."""
    base = now or datetime.now(UTC)
    return (base + timedelta(seconds=retain_s)).isoformat()


def cleanup_expired(*, root: Path) -> int:
    """Walk ``root``, delete WAVs whose ``written_at_utc + retain_s < now``.

    The deadline is read from the meta sidecar (``written_at_utc`` field),
    not the filesystem mtime, so it survives backup/restore. Returns
    count removed. Safe to call on a nonexistent root.
    """
    if not root.exists():
        return 0
    now = datetime.now(UTC)
    removed = 0
    for meta_path in root.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
            written = datetime.fromisoformat(meta["written_at_utc"])
            retain_s = float(meta["retain_s"])
        except (OSError, ValueError, KeyError) as exc:
            log.warning("skipping malformed meta %s: %s", meta_path, exc)
            continue
        if written + timedelta(seconds=retain_s) < now:
            wav_path = meta_path.with_suffix("").with_suffix(".wav")
            try:
                if wav_path.exists():
                    wav_path.unlink()
                meta_path.unlink()
                removed += 1
            except OSError as exc:
                log.warning("failed to remove %s: %s", wav_path, exc)
    return removed


__all__ = ["cleanup_expired", "retain_until_iso", "write_addressed"]
