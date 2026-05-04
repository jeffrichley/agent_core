"""SessionRegistry tests — TTL, consume, lookup error cases.

The registry is the seam T13 (submit handler) and T14 (MCP-tool wiring)
will use to look up in-flight sessions. These tests pin the shape of
that contract: tokens are unique per ``create`` call, expired sessions
raise loudly, ``consume`` is one-shot, and ``__len__`` reports only
active (non-consumed, non-expired) sessions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agent_core_briefs.session import (
    ComposeSession,
    SessionConsumedError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRegistry,
)


def _make_session(*, created_at: datetime | None = None) -> ComposeSession:
    """Build a minimal ComposeSession for registry tests.

    Defaults ``created_at`` to ``now(UTC)`` so the session is fresh; tests
    that need an expired session pass an explicit older timestamp.
    """
    if created_at is None:
        created_at = datetime.now(UTC)
    return ComposeSession(
        brief_type="morning_brief",
        playbook_path=Path("/nonexistent.md"),
        voice="test",
        scope="today",
        when=datetime.now(UTC),
        context={},
        sections_required=[],
        sections_optional=[],
        sections_conditional_active=[],
        target_agent="agent",
        correlation_id="c1",
        metadata={},
        sections=[],
        colors_palette={},
        created_at=created_at,
    )


def test_create_returns_unique_token():
    """Two creates produce distinct 32-char hex tokens stored under their own keys."""
    registry = SessionRegistry()
    t1 = registry.create(_make_session())
    t2 = registry.create(_make_session())
    assert t1 != t2
    assert len(t1) == 32
    assert len(t2) == 32
    # Both tokens look up their own session (not a shared instance).
    assert registry.get(t1) is not registry.get(t2)


def test_get_returns_session():
    """Happy path — get() returns the same session that was inserted."""
    registry = SessionRegistry()
    session = _make_session()
    token = registry.create(session)
    assert registry.get(token) is session


def test_get_unknown_raises():
    """Unknown token raises SessionNotFoundError (a KeyError subtype)."""
    registry = SessionRegistry()
    with pytest.raises(SessionNotFoundError):
        registry.get("deadbeef" * 4)
    # Subclass of KeyError so callers that broadly catch KeyError still work.
    with pytest.raises(KeyError):
        registry.get("deadbeef" * 4)


def test_get_expired_raises():
    """A session whose age exceeds ttl_seconds raises SessionExpiredError."""
    registry = SessionRegistry(ttl_seconds=10.0)
    old = datetime.now(UTC) - timedelta(seconds=60)
    token = registry.create(_make_session(created_at=old))
    with pytest.raises(SessionExpiredError):
        registry.get(token)
    # The expired entry was swept on access — registry shouldn't claim it
    # exists on a second lookup either.
    with pytest.raises(SessionNotFoundError):
        registry.get(token)


def test_get_consumed_raises():
    """After consume(), subsequent get() raises SessionConsumedError."""
    registry = SessionRegistry()
    token = registry.create(_make_session())
    registry.consume(token)
    with pytest.raises(SessionConsumedError):
        registry.get(token)


def test_consume_marks_session_consumed():
    """First consume returns the session; second raises SessionConsumedError."""
    registry = SessionRegistry()
    session = _make_session()
    token = registry.create(session)
    consumed = registry.consume(token)
    assert consumed is session
    assert consumed.consumed is True
    with pytest.raises(SessionConsumedError):
        registry.consume(token)


def test_cleanup_expired_removes_old_sessions():
    """cleanup_expired returns the count of removed sessions and shrinks the registry."""
    registry = SessionRegistry(ttl_seconds=10.0)
    fresh_token = registry.create(_make_session())
    old_token_1 = registry.create(
        _make_session(created_at=datetime.now(UTC) - timedelta(seconds=60))
    )
    old_token_2 = registry.create(
        _make_session(created_at=datetime.now(UTC) - timedelta(seconds=120))
    )

    removed = registry.cleanup_expired()
    assert removed == 2
    assert len(registry) == 1
    # Fresh session still accessible; old ones are gone.
    assert registry.get(fresh_token) is not None
    with pytest.raises(SessionNotFoundError):
        registry.get(old_token_1)
    with pytest.raises(SessionNotFoundError):
        registry.get(old_token_2)


def test_len_counts_only_active_sessions():
    """__len__ excludes expired + consumed sessions."""
    registry = SessionRegistry(ttl_seconds=10.0)
    active = registry.create(_make_session())
    expired = registry.create(_make_session(created_at=datetime.now(UTC) - timedelta(seconds=60)))
    consumed = registry.create(_make_session())
    registry.consume(consumed)

    assert len(registry) == 1  # only the active one
    # Sanity: the active token still resolves.
    assert registry.get(active) is not None
    # Expired was opportunistically swept by __len__.
    with pytest.raises(SessionNotFoundError):
        registry.get(expired)


def test_cleanup_expired_with_explicit_now():
    """cleanup_expired accepts an explicit ``now`` for deterministic tests."""
    registry = SessionRegistry(ttl_seconds=10.0)
    base = datetime.now(UTC)
    token = registry.create(_make_session(created_at=base))
    # 5s after creation: session is still fresh — cleanup is a no-op.
    assert registry.cleanup_expired(now=base + timedelta(seconds=5)) == 0
    assert registry.get(token) is not None
    # 30s after: expired → cleaned up.
    assert registry.cleanup_expired(now=base + timedelta(seconds=30)) == 1
    with pytest.raises(SessionNotFoundError):
        registry.get(token)
