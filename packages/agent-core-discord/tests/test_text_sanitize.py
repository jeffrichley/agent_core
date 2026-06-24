"""Unit tests for agent_core_discord.text_sanitize.scrub_surrogates()."""

from __future__ import annotations

import json

import pytest
from agent_core_discord.text_sanitize import scrub_surrogates


def test_lone_high_surrogate_replaced_by_replacement_char():
    """A lone high surrogate (U+D800–U+DBFF) must become U+FFFD (one or more).

    Python's UTF-8 decoder maps each invalid byte in the WTF-8 representation
    of a lone surrogate to U+FFFD individually, so the result may be multiple
    replacement characters.  The important invariants are: the original lone
    surrogate is absent, at least one U+FFFD is present, and the result
    round-trips safely through JSON and UTF-8.
    """
    dirty = "\ud83c"  # lone high surrogate — no paired low surrogate follows
    result = scrub_surrogates(dirty)
    assert "\ud83c" not in result
    assert "�" in result  # at least one replacement character present
    # Must be JSON-safe and valid UTF-8.
    json.dumps(result)
    result.encode("utf-8")


def test_lone_low_surrogate_replaced_by_replacement_char():
    """A lone low surrogate (U+DC00–U+DFFF) must also become U+FFFD (one or more)."""
    dirty = "\udc00"  # lone low surrogate with no preceding high surrogate
    result = scrub_surrogates(dirty)
    assert "\udc00" not in result
    assert "�" in result
    json.dumps(result)
    result.encode("utf-8")


def test_clean_text_passes_through_unchanged():
    """Clean ASCII + multi-byte text must not be altered."""
    text = "Hello, world! 🎉 こんにちは"
    assert scrub_surrogates(text) == text


def test_valid_surrogate_pair_in_narrow_build_is_already_clean():
    """A properly encoded supplementary character (e.g. 😀 U+1F600) round-trips."""
    text = "emoji: \U0001F600"
    result = scrub_surrogates(text)
    assert result == text


def test_json_round_trip_after_scrub():
    """Result of scrub_surrogates() must be JSON-serialisable without error."""
    dirty = "before \ud83c after"
    cleaned = scrub_surrogates(dirty)
    serialised = json.dumps(cleaned)
    assert json.loads(serialised) == cleaned


def test_utf8_encode_after_scrub():
    """Result of scrub_surrogates() must encode to UTF-8 without error."""
    dirty = "\ud83c"
    cleaned = scrub_surrogates(dirty)
    cleaned.encode("utf-8")  # must not raise


def test_flag_emoji_sliced_surrogate_case():
    """Regression: lone surrogate produced by slicing the UTF-16 encoding
    of U+1F3F4 (BLACK FLAG) at a buffer boundary.

    U+1F3F4 encodes as the surrogate pair (U+D83C, U+DFF4).  A buffer slice
    that cuts between them yields the lone high surrogate U+D83C.  The same
    defect appears with subdivision-tag flag sequences relayed through Discord.
    This test constructs the offending code point programmatically.
    """
    # Build the lone high surrogate by slicing the UTF-16-LE encoding of
    # U+1F3F4 (BLACK FLAG) at the 2-byte mark, then decoding only those
    # first two bytes (the high-surrogate word D83C) with surrogatepass.
    # This is the actual vector: a buffer slice cutting mid-surrogate-pair
    # leaves the high surrogate stranded.  The Pepper incident on 2026-06-24
    # involved the England flag, which uses a similar subdivision-tag sequence
    # whose UTF-16 encoding also starts with D83C.
    black_flag = "\U0001F3F4"
    # UTF-16-LE encodes U+1F3F4 as 4 bytes: D83C (high) + DFF4 (low).
    # Take only the first 2 bytes — the high-surrogate code unit — and decode
    # back to a Python str via surrogatepass, yielding a lone U+D83C.
    utf16_bytes = black_flag.encode("utf-16-le")
    assert len(utf16_bytes) == 4  # supplementary char → surrogate pair → 4 bytes
    lone_high = utf16_bytes[:2].decode("utf-16-le", "surrogatepass")
    assert len(lone_high) == 1  # a single lone high surrogate code unit

    dirty = f"flag: {lone_high} end"
    result = scrub_surrogates(dirty)

    # Must not raise and result must be JSON-safe.
    json.dumps(result)
    result.encode("utf-8")
    # The lone surrogate must have been replaced (not silently deleted).
    assert "�" in result
    # The framing text must survive.
    assert result.startswith("flag: ")
    assert result.endswith(" end")
