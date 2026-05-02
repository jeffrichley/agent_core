"""Split outbound Discord text into API-safe chunks.

Ports the spirit of Pepper's ``smart_chunk`` with Discord-oriented limits:
each chunk is at most ``DISCORD_CONTENT_LIMIT`` characters (Discord's hard
cap). Splits prefer natural boundaries in the last ``TAIL_SEARCH`` chars of
each chunk window, then sentence endings, words, and finally a hard cut.

See GitHub issue #20 for rationale (2000-char API reject, bus-level ack).
"""

from __future__ import annotations

# Discord HTTP API maximum for message ``content``.
DISCORD_CONTENT_LIMIT = 2000
# Target size before continuation markers / headroom.
DISCORD_CHUNK_TARGET = 1900
# When hunting for a soft boundary, prefer breaks in this tail region.
TAIL_SEARCH = 200
# Safety valve — avoid spamming the channel on pathological payloads.
MAX_CHUNKS = 25


def _find_soft_split(text: str, limit: int) -> int:
    """Return index in ``text`` where the first chunk should end (exclusive).

    ``limit`` is the maximum length of the first piece. Search order in the
    tail window first, then the full prefix, then hard ``limit``.
    """
    if len(text) <= limit:
        return len(text)

    window = text[:limit]
    lo = max(0, limit - TAIL_SEARCH)

    def in_tail(idx: int) -> bool:
        return idx > lo and idx > 0

    pos = window.rfind("\n\n", lo, limit)
    if pos != -1 and in_tail(pos + 1):
        return pos + 1

    pos = window.rfind("\n", lo, limit)
    if pos != -1 and in_tail(pos + 1):
        return pos + 1

    for sep in (". ", "! ", "? "):
        pos = window.rfind(sep, lo, limit)
        if pos != -1 and in_tail(pos + len(sep)):
            return pos + len(sep)

    pos = window.rfind(" ", lo, limit)
    if pos != -1 and in_tail(pos + 1):
        return pos + 1

    pos = window.rfind("\n\n", 0, limit)
    if pos != -1:
        return pos + 1
    pos = window.rfind("\n", 0, limit)
    if pos != -1:
        return pos + 1
    for sep in (". ", "! ", "? "):
        pos = window.rfind(sep, 0, limit)
        if pos != -1:
            return pos + len(sep)
    pos = window.rfind(" ", 0, limit)
    if pos != -1:
        return pos + 1

    return limit


def smart_chunk_discord(text: str, *, limit: int = DISCORD_CHUNK_TARGET) -> list[str]:
    """Split ``text`` into chunks each ≤ ``DISCORD_CONTENT_LIMIT``.

    Continuation markers ``(i/n)`` prefix chunks after the first when they fit
    under the Discord cap.

    Raises:
        ValueError: if more than ``MAX_CHUNKS`` would be required.
    """
    if len(text) <= limit:
        out = [text]
        _validate_chunks(out)
        return out

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = _find_soft_split(remaining, limit)
        if split_at <= 0:
            split_at = min(limit, len(remaining))
        # Only trim newlines so spaces at soft boundaries (e.g. word splits) are
        # not dropped across chunks.
        piece = remaining[:split_at].rstrip("\n")
        if not piece:
            split_at = min(limit, len(remaining))
            piece = remaining[:split_at]
        chunks.append(piece)
        remaining = remaining[split_at:].lstrip("\n")

    if len(chunks) <= 1:
        _validate_chunks(chunks)
        return chunks

    n = len(chunks)
    marked: list[str] = [chunks[0]]
    for i, c in enumerate(chunks[1:], start=2):
        marker = f"({i}/{n})\n"
        combined = marker + c
        if len(combined) <= DISCORD_CONTENT_LIMIT:
            marked.append(combined)
        else:
            marked.append(c)

    _validate_chunks(marked)
    return marked


def _validate_chunks(chunks: list[str]) -> None:
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(
            f"message would require {len(chunks)} Discord chunks (max {MAX_CHUNKS}); "
            "refuse to spam the channel"
        )
    for c in chunks:
        if len(c) > DISCORD_CONTENT_LIMIT:
            raise ValueError(
                f"internal chunking error: chunk len {len(c)} > {DISCORD_CONTENT_LIMIT}"
            )
