"""Structured JSON logging + correlation-id contextvar for agent_core.

Provides:
- ``correlation_id`` — a ``ContextVar[str]`` that carries the active envelope's
  correlation id across all log calls during dispatch, without modifying call sites.
- ``bind_correlation_id(value)`` — thin wrapper around ``correlation_id.set()``.
  Returns a ``Token`` that callers must reset in a ``finally`` block to restore
  the previous value and prevent id bleed across consecutive dispatches.
- ``CorrelationIdFilter`` — a ``logging.Filter`` that stamps every passing
  ``LogRecord`` with ``record.correlation_id = correlation_id.get()``.  Attached
  to the root handler so the field is present for both formatters.
- ``JsonFormatter`` — a ``logging.Formatter`` subclass that serialises records to
  a single-line JSON object carrying ``timestamp``, ``level``, ``logger``,
  ``message``, ``correlation_id``, and (when non-empty) ``exc_info``.
- ``configure_logging(mode)`` — replaces the root logger's handlers with a single
  ``StreamHandler`` using either ``JsonFormatter`` (json) or the human-readable
  format string (pretty).  Both modes attach ``CorrelationIdFilter``.

No third-party dependencies — uses stdlib ``logging``, ``contextvars``, and ``json``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from contextvars import ContextVar, Token
from typing import Literal

# ---------------------------------------------------------------------------
# ContextVar
# ---------------------------------------------------------------------------

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def bind_correlation_id(value: str) -> Token[str]:
    """Set ``correlation_id`` to *value* and return the reset token.

    Callers MUST call ``correlation_id.reset(token)`` in a ``finally`` block
    to restore the previous value and prevent id bleed across consecutive
    dispatches running in the same event-loop task.
    """
    return correlation_id.set(value)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class CorrelationIdFilter(logging.Filter):
    """Stamps every LogRecord with the active ``correlation_id`` contextvar value.

    Attached to the root handler by ``configure_logging`` so the field is always
    present regardless of which formatter is selected.  Call sites need no changes.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id.get()
        return True


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Serialises a LogRecord to a single-line JSON object.

    Output fields (always present):
        timestamp    — ISO-8601 string (``record.created`` → asctime via
                        ``self.formatTime``; uses default datefmt)
        level        — ``record.levelname``
        logger       — ``record.name``
        message      — formatted message string (args interpolated)
        correlation_id — value stamped by ``CorrelationIdFilter``

    Optional field:
        exc_info — formatted traceback string, present only when
                   ``record.exc_info`` is truthy.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Interpolate %(message)s so record.getMessage() is called exactly once.
        record.message = record.getMessage()
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "correlation_id": getattr(record, "correlation_id", ""),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------

_PRETTY_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(mode: Literal["json", "pretty"]) -> None:
    """Replace the root logger's handlers with a single StreamHandler.

    Both modes attach ``CorrelationIdFilter`` so ``record.correlation_id`` is
    always populated.  The formatter is ``JsonFormatter`` for *mode='json'* or
    a plain ``logging.Formatter`` with the historical pretty format for
    *mode='pretty'*.

    Removes all pre-existing root handlers before installing the new one so
    that repeated calls (e.g., in tests) are idempotent.
    """
    root = logging.getLogger()
    # Clear all existing handlers.
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())

    if mode == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_PRETTY_FORMAT))

    root.addHandler(handler)
    root.setLevel(logging.INFO)
