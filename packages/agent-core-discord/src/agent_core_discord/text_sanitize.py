"""Unicode sanitization utilities for Discord inbound text.

The primary concern is lone UTF-16 surrogate code units (U+D800–U+DFFF)
that can enter the system when Discord relays content containing flag emojis
built from subdivision-tag sequences, or when a buffer slice cuts across a
surrogate pair.  A lone surrogate is valid in a Python ``str`` but is
ill-formed UTF-8/JSON and causes the Anthropic API to reject requests with::

    400 The request body is not valid JSON: invalid high surrogate in string

The remedy is to scrub at the earliest possible boundary (Discord inbound)
before the text enters a bus envelope, replacing each ill-formed code unit
with U+FFFD (the Unicode replacement character) so the failure is visible
but non-fatal.
"""

from __future__ import annotations


def scrub_surrogates(s: str) -> str:
    """Replace any lone UTF-16 surrogate code units in *s* with U+FFFD.

    Python ``str`` objects can hold lone surrogates (e.g. ``"\\ud83c"``).
    Such strings cannot be serialised to valid UTF-8 or JSON and are rejected
    by the Anthropic API.  This function maps every ill-formed code unit to
    the Unicode replacement character ``�`` (``"\\ufffd"``), returning a
    clean ``str`` that round-trips safely through ``json.dumps`` and
    ``.encode("utf-8")``.

    The round-trip ``s.encode("utf-8", "surrogatepass").decode("utf-8",
    "replace")`` exploits the fact that ``surrogatepass`` on the *encode*
    side emits the WTF-8 byte sequence for each lone surrogate (which is
    invalid UTF-8), and ``replace`` on the *decode* side then maps those
    invalid byte sequences to U+FFFD.  Using ``errors="replace"`` on the
    *encode* step instead would substitute ASCII ``?`` (0x3F), which is
    visible but less semantically correct.

    Clean text (no lone surrogates) is returned unchanged.
    """
    return s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
