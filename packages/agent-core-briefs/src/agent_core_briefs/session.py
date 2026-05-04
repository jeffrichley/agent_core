"""Session registry for in-flight compose sessions.

The orchestrator (T9) creates a :class:`ComposeSession` for every accepted
``BriefRequest`` and hands the agent a token. Between wake-up and submit the
agent calls back via the agent-tool surface (T10's ``tools.py``) — these
tools look up the session by token and operate on its data. Eventually T13
(submit handler) calls :meth:`SessionRegistry.consume` to retire the session
in one shot.

Token semantics
---------------
Tokens are 32-char hex strings produced by :func:`secrets.token_hex(16)`.
Each token is single-use: once :meth:`SessionRegistry.consume` has been
called, subsequent ``get``/``consume`` calls on the same token raise
:class:`SessionConsumedError`. Sessions also expire ``ttl_seconds`` after
creation; expired tokens raise :class:`SessionExpiredError` and unknown
tokens raise :class:`SessionNotFoundError`.

Thread-safety
-------------
Not required for v1 — the bus loop runs the orchestrator and the MCP-tool
callbacks in the same event loop. If cross-thread access becomes a concern
(e.g., a future scheduled cleanup task running outside the bus loop), wrap
the registry's mutating methods in a lock.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_core_briefs.protocol import SectionSpec


class SessionNotFoundError(KeyError):
    """Raised when a session token is unknown to the registry."""


class SessionExpiredError(ValueError):
    """Raised when a session token's TTL has elapsed."""


class SessionConsumedError(ValueError):
    """Raised when a session has already been consumed (post-submit)."""


@dataclass
class ComposeSession:
    """In-flight compose session tracked by the orchestrator.

    Carries enough state for the agent-tool surface to answer questions
    about the brief (sections, fields, palette) without re-parsing the
    playbook. ``sections`` and ``colors_palette`` are populated by the
    orchestrator at session-build time so per-tool calls don't repeat I/O.

    ``extension_sections`` accumulates ad-hoc sections added via
    :func:`agent_core_briefs.tools.add_extension_section`; T11/T12
    destinations append these to the section list before rendering.
    """

    brief_type: str
    playbook_path: Path
    voice: str
    scope: str | None
    when: datetime
    context: dict[str, Any]
    sections_required: list[str]
    sections_optional: list[str]
    sections_conditional_active: list[str]
    target_agent: str
    correlation_id: str
    metadata: dict[str, Any]
    sections: list[SectionSpec]
    colors_palette: dict[str, int]
    created_at: datetime
    consumed: bool = False
    extension_sections: list[SectionSpec] = field(default_factory=list)


class SessionRegistry:
    """In-memory session token store with TTL and one-shot consume.

    Storage is a plain dict; expiry checks are lazy (every ``get``/``consume``
    sweeps any expired entries it walks past). Callers can run a manual
    sweep via :meth:`cleanup_expired` — T16 may wire this to a scheduled
    task, but v1 doesn't need one because the registry is never large.
    """

    def __init__(self, *, ttl_seconds: float = 1800.0):
        self._ttl_seconds = float(ttl_seconds)
        self._sessions: dict[str, ComposeSession] = {}

    # ---- mutators ----
    def create(self, session: ComposeSession) -> str:
        """Generate a fresh session_token, store the session, return the token.

        Collision-safe: regenerates on the (vanishingly unlikely) event that
        :func:`secrets.token_hex(16)` returns a token already in use.
        """
        while True:
            token = secrets.token_hex(16)
            if token not in self._sessions:
                break
        self._sessions[token] = session
        return token

    def consume(self, token: str) -> ComposeSession:
        """Look up the session, mark it consumed, return it (one-shot).

        Subsequent ``get``/``consume`` calls on the same token raise
        :class:`SessionConsumedError`.
        """
        session = self.get(token)
        session.consumed = True
        return session

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        """Sweep expired sessions; return count removed.

        Cheap; the registry is never large. Safe to call at any time.
        """
        if now is None:
            now = datetime.now(UTC)
        expired = [tok for tok, sess in self._sessions.items() if self._is_expired(sess, now)]
        for tok in expired:
            del self._sessions[tok]
        return len(expired)

    # ---- accessors ----
    def get(self, token: str) -> ComposeSession:
        """Look up a non-consumed, non-expired session.

        Raises :class:`SessionNotFoundError`, :class:`SessionExpiredError`,
        or :class:`SessionConsumedError`.
        """
        session = self._sessions.get(token)
        if session is None:
            raise SessionNotFoundError(token)
        now = datetime.now(UTC)
        if self._is_expired(session, now):
            # Drop the expired entry on the floor before signaling — keeps
            # the registry small without a separate sweep.
            del self._sessions[token]
            raise SessionExpiredError(token)
        if session.consumed:
            raise SessionConsumedError(token)
        return session

    def __len__(self) -> int:
        """Count active (non-expired, non-consumed) sessions.

        Drops expired entries opportunistically so the count is honest.
        """
        now = datetime.now(UTC)
        # Drop expired entries opportunistically.
        expired = [tok for tok, sess in self._sessions.items() if self._is_expired(sess, now)]
        for tok in expired:
            del self._sessions[tok]
        return sum(1 for sess in self._sessions.values() if not sess.consumed)

    # ---- private ----
    def _is_expired(self, session: ComposeSession, now: datetime) -> bool:
        return (now - session.created_at).total_seconds() > self._ttl_seconds


__all__ = [
    "ComposeSession",
    "SessionConsumedError",
    "SessionExpiredError",
    "SessionNotFoundError",
    "SessionRegistry",
]
