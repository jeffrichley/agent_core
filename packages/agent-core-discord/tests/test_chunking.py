"""Tests for Discord outbound message chunking."""

from __future__ import annotations

import pytest
from agent_core_discord.chunking import (
    DISCORD_CHUNK_TARGET,
    MAX_CHUNKS,
    smart_chunk_discord,
)


def _content_only(chunks: list[str]) -> str:
    """Reassemble logical text, dropping ``(i/n)`` continuation prefixes."""
    parts: list[str] = []
    for i, c in enumerate(chunks):
        if i > 0 and c.startswith("(") and ")\n" in c[:16]:
            parts.append(c.split("\n", 1)[-1])
        else:
            parts.append(c)
    return "".join(parts)


def test_short_message_no_split():
    text = "Hello, world!"
    result = smart_chunk_discord(text)
    assert result == ["Hello, world!"]


def test_split_at_paragraph():
    para1 = "A" * 40
    para2 = "B" * 40
    text = f"{para1}\n\n{para2}"
    result = smart_chunk_discord(text, limit=50)
    assert len(result) == 2
    body = _content_only(result)
    assert body.count("A") == 40 and body.count("B") == 40


def test_split_at_newline():
    line1 = "A" * 40
    line2 = "B" * 40
    text = f"{line1}\n{line2}"
    result = smart_chunk_discord(text, limit=50)
    assert len(result) == 2
    body = _content_only(result)
    assert body.count("A") == 40 and body.count("B") == 40


def test_split_at_space():
    text = ("word " * 20).strip()
    result = smart_chunk_discord(text, limit=30)
    for chunk in result:
        assert len(chunk) <= 2000
    assert _content_only(result) == text


def test_hard_cut_no_boundaries():
    text = "A" * 100
    result = smart_chunk_discord(text, limit=30)
    assert len(result) == 4
    assert all(len(c) <= 2000 for c in result)
    assert _content_only(result) == text


def test_no_chunk_exceeds_discord_cap():
    text = (
        "Short paragraph.\n\n"
        + "A" * 1500
        + "\n\n"
        + "Another paragraph with some words.\n"
        + "B" * 500
    )
    result = smart_chunk_discord(text, limit=DISCORD_CHUNK_TARGET)
    for chunk in result:
        assert len(chunk) <= 2000


def test_too_many_chunks_raises():
    text = "x" * (DISCORD_CHUNK_TARGET * (MAX_CHUNKS + 3))
    with pytest.raises(ValueError, match="max"):
        smart_chunk_discord(text)
