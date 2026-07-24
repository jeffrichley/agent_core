"""File lifecycle for voice WAVs: content-addressed write + TTL cleanup."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def transcode_audio(wav_bytes: bytes, *, target_format: str) -> bytes:
    """Transcode WAV bytes to the requested format.

    Returns ``wav_bytes`` unchanged for ``"wav"``. Encodes to OGG Vorbis
    using ``soundfile`` for ``"ogg"``. Shells out to ``ffmpeg`` for ``"mp3"``.

    Raises:
        ValueError: if ``target_format`` is not ``"wav"``, ``"mp3"``, or ``"ogg"``.
        RuntimeError: if ffmpeg is not on PATH or exits non-zero.
    """
    if target_format == "wav":
        return wav_bytes

    if target_format == "ogg":
        import soundfile as sf  # type: ignore[import-untyped]  # soundfile ships no stubs

        with sf.SoundFile(io.BytesIO(wav_bytes)) as f:
            audio_data = f.read(dtype="float32")
            sample_rate = f.samplerate
        buf = io.BytesIO()
        sf.write(buf, audio_data, sample_rate, format="OGG", subtype="VORBIS")
        return buf.getvalue()

    if target_format == "mp3":
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-f", "wav",
                    "-i", "pipe:0",
                    "-f", "mp3",
                    "-b:a", "128k",
                    "pipe:1",
                ],
                input=wav_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"ffmpeg not found on PATH: {exc}") from exc

        if result.returncode != 0:
            stderr_fragment = (result.stderr or b"")[:500].decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ffmpeg exited with code {result.returncode}: {stderr_fragment}"
            )
        return result.stdout

    raise ValueError(f"unknown target_format {target_format!r}; expected 'wav', 'mp3', or 'ogg'")


def write_addressed(
    audio: bytes,
    *,
    root: Path,
    retain_s: float,
    ext: str = "wav",
) -> tuple[Path, str]:
    """Write ``audio`` to ``<root>/<sha256>.<ext>`` with a meta sidecar.

    Returns (path, sha256_hex). The audio body is content-addressed and
    only written if not already present (so identical audio dedupes).
    The meta sidecar IS rewritten on every call, refreshing
    ``written_at_utc`` and effectively resetting the TTL window —
    LRU-ish semantics: actively-touched files stay alive longer.

    The meta JSON includes an ``"ext"`` key so ``cleanup_expired()`` can
    locate the companion audio file regardless of format.
    """
    sha = hashlib.sha256(audio).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    audio_path = root / f"{sha}.{ext}"
    meta_path = root / f"{sha}.meta.json"

    if not audio_path.exists():
        audio_path.write_bytes(audio)

    meta = {
        "sha256": sha,
        "retain_s": retain_s,
        "written_at_utc": datetime.now(UTC).isoformat(),
        "ext": ext,
    }
    meta_path.write_text(json.dumps(meta))
    return audio_path, sha


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
            ext = meta.get("ext", "wav")
            audio_path = meta_path.with_suffix("").with_suffix(f".{ext}")
            try:
                if audio_path.exists():
                    audio_path.unlink()
                meta_path.unlink()
                removed += 1
            except OSError as exc:
                log.warning("failed to remove %s: %s", audio_path, exc)
    return removed


__all__ = ["cleanup_expired", "retain_until_iso", "transcode_audio", "write_addressed"]
