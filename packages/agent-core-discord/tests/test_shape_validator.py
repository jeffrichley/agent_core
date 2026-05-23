"""Unit tests for shape_validator — the pure-function recognized-shape catalog
for the Discord adapter's strict-mode envelope validation.

The validator is PURE: no I/O, no Discord client, no chrome.* / discord.*
imports. These tests construct envelopes directly and assert on the
returned ShapeValidation value.
"""

from datetime import UTC, datetime

import pytest

from agent_core.bus.envelope import (
    Envelope,
    TextMessagePayload,
    ToolInvocationPayload,
)
from agent_core_discord.shape_validator import (
    Recognized,
    Unrecognized,
    validate,
)


def _make_env(*, kind, payload, metadata=None, from_="test-sender"):
    """Helper: build an Envelope with sensible defaults for validator tests."""
    return Envelope(
        id="env-abc",
        correlation_id="corr-1",
        to="discord-test",
        kind=kind,
        payload=payload,
        metadata=metadata or {},
        created_at=datetime.now(UTC),
        from_=from_,
    )


def test_recognized_and_unrecognized_are_frozen_dataclasses():
    """Both ShapeValidation variants are frozen so test assertions can compare
    by value and Recognized/Unrecognized can be used as dict keys later."""
    r = Recognized(shape_name="x", deprecation_log_line=None)
    u = Unrecognized(fields=["a"], canonical_equivalent="b")
    with pytest.raises(Exception):
        r.shape_name = "y"  # frozen
    with pytest.raises(Exception):
        u.fields = ["c"]    # frozen
