"""Split outbound Discord text into API-safe chunks.

Ports the spirit of Pepper's ``smart_chunk`` with Discord-oriented limits:
each chunk is at most ``DISCORD_CONTENT_LIMIT`` characters (Discord's hard
cap). Splits prefer natural boundaries in the last ``TAIL_SEARCH`` chars of
each chunk window, then sentence endings, words, and finally a hard cut.

See GitHub issue #20 for rationale (2000-char API reject, bus-level ack).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Discord HTTP API maximum for message ``content``.
DISCORD_CONTENT_LIMIT = 2000
# Target size before continuation markers / headroom.
DISCORD_CHUNK_TARGET = 1900
# When hunting for a soft boundary, prefer breaks in this tail region.
TAIL_SEARCH = 200
# Safety valve — avoid spamming the channel on pathological payloads.
MAX_CHUNKS = 25

_LINK_OR_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)\n]*\)|\[[^\]]+]\([^)\n]*\)")


@dataclass(frozen=True)
class _MarkdownState:
    in_fence: bool = False
    fence_delim: str = "```"
    fence_lang: str = ""
    in_inline_code: bool = False


def _scan_markdown_states(text: str) -> list[_MarkdownState]:
    """Return parser state at every split boundary in ``text``."""
    states: list[_MarkdownState] = []
    in_fence = False
    fence_delim = "```"
    fence_lang = ""
    inline_ticks = 0
    line_start = True
    n = len(text)

    for i, ch in enumerate(text):
        states.append(
            _MarkdownState(
                in_fence=in_fence,
                fence_delim=fence_delim,
                fence_lang=fence_lang,
                in_inline_code=inline_ticks > 0,
            )
        )

        if line_start and inline_ticks == 0:
            j = i
            indent = 0
            while j < n and text[j] in (" ", "\t") and indent < 3:
                j += 1
                indent += 1
            if j + 2 < n and text[j] == text[j + 1] == text[j + 2] and text[j] in ("`", "~"):
                run_char = text[j]
                k = j
                while k < n and text[k] == run_char:
                    k += 1
                delim = text[j:k]
                line_end = text.find("\n", k)
                if line_end == -1:
                    line_end = n
                trailer = text[k:line_end]
                if not in_fence:
                    in_fence = True
                    fence_delim = delim
                    fence_lang = trailer.strip()
                elif run_char == fence_delim[0] and len(delim) >= len(fence_delim):
                    in_fence = False
                    fence_lang = ""

        if not in_fence and ch == "`" and (i == 0 or text[i - 1] != "`"):
            k = i
            while k < n and text[k] == "`":
                k += 1
            run = k - i
            if inline_ticks == 0:
                inline_ticks = run
            elif run == inline_ticks:
                inline_ticks = 0

        line_start = ch == "\n"

    states.append(
        _MarkdownState(
            in_fence=in_fence,
            fence_delim=fence_delim,
            fence_lang=fence_lang,
            in_inline_code=inline_ticks > 0,
        )
    )
    return states


def _link_boundary_mask(text: str) -> list[bool]:
    """Boundary mask of positions that fall inside markdown link/image tokens."""
    mask = [False] * (len(text) + 1)
    for match in _LINK_OR_IMAGE_RE.finditer(text):
        for idx in range(match.start() + 1, match.end()):
            mask[idx] = True
    return mask


def _fence_opening(state: _MarkdownState) -> str:
    if not state.in_fence:
        return ""
    lang = f" {state.fence_lang}" if state.fence_lang else ""
    return f"{state.fence_delim}{lang}\n"


def _fence_closing(state: _MarkdownState) -> str:
    if not state.in_fence:
        return ""
    return f"\n{state.fence_delim}"


def _is_safe_boundary(
    abs_idx: int,
    states: list[_MarkdownState],
    in_link_mask: list[bool],
) -> bool:
    st = states[abs_idx]
    if st.in_inline_code:
        return False
    if in_link_mask[abs_idx]:
        return False
    return True


def _find_soft_split_with_markdown(
    text: str,
    start: int,
    limit: int,
    states: list[_MarkdownState],
    in_link_mask: list[bool],
) -> int:
    """Markdown-aware split index in absolute coordinates."""
    end = min(len(text), start + limit)
    if end >= len(text):
        return len(text)

    window = text[start:end]
    lo = max(0, len(window) - TAIL_SEARCH)

    def boundary_ok(rel_idx: int) -> bool:
        if rel_idx <= 0:
            return False
        return _is_safe_boundary(start + rel_idx, states, in_link_mask)

    def find_rfind(needle: str, start_rel: int, end_rel: int, add: int) -> int:
        scan = end_rel
        while True:
            pos = window.rfind(needle, start_rel, scan)
            if pos == -1:
                return -1
            candidate = pos + add
            if boundary_ok(candidate):
                return candidate
            scan = pos

    checks: list[tuple[str, int]] = [("\n\n", 1), ("\n", 1), (". ", 2), ("! ", 2), ("? ", 2), (" ", 1)]

    for sep, add in checks:
        pos = find_rfind(sep, lo, len(window), add)
        if pos != -1:
            return start + pos
    for sep, add in checks:
        pos = find_rfind(sep, 0, len(window), add)
        if pos != -1:
            return start + pos

    hard = end
    while hard > start and not boundary_ok(hard - start):
        hard -= 1
    return hard if hard > start else end


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

    states = _scan_markdown_states(text)
    in_link_mask = _link_boundary_mask(text)
    chunks: list[str] = []
    pos = 0
    n = len(text)

    while pos < n:
        if n - pos <= limit:
            split_at = n
        else:
            split_at = _find_soft_split_with_markdown(text, pos, limit, states, in_link_mask)
            if split_at <= pos:
                split_at = min(pos + limit, n)

        start_state = states[pos]
        end_state = states[split_at]
        prefix = _fence_opening(start_state)
        suffix = _fence_closing(end_state)
        piece = text[pos:split_at]

        if not start_state.in_fence and not end_state.in_fence:
            trimmed = piece.rstrip("\n")
            trim_delta = len(piece) - len(trimmed)
            piece = trimmed or piece
            split_at -= trim_delta

        rendered = f"{prefix}{piece}{suffix}"
        if len(rendered) > DISCORD_CONTENT_LIMIT and split_at - pos > 1:
            # Last-resort shrink for pathological cases (long markdown token).
            fallback = split_at - 1
            while fallback > pos:
                if _is_safe_boundary(fallback, states, in_link_mask):
                    trial = f"{prefix}{text[pos:fallback]}{_fence_closing(states[fallback])}"
                    if len(trial) <= DISCORD_CONTENT_LIMIT:
                        split_at = fallback
                        end_state = states[split_at]
                        suffix = _fence_closing(end_state)
                        piece = text[pos:split_at]
                        rendered = f"{prefix}{piece}{suffix}"
                        break
                fallback -= 1

        if not piece:
            split_at = min(pos + limit, n)
            end_state = states[split_at]
            suffix = _fence_closing(end_state)
            piece = text[pos:split_at]
            rendered = f"{prefix}{piece}{suffix}"

        chunks.append(rendered)
        pos = split_at
        if not end_state.in_fence:
            while pos < n and text[pos] == "\n":
                pos += 1

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
