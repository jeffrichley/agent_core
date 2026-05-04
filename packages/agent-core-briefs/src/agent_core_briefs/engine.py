"""Brief framework engine: gather + submit orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_core_briefs.protocol import Fetcher


@dataclass(frozen=True)
class FetcherInvocation:
    """One configured fetcher call: instance + config + per-call timeout."""

    fetcher: Fetcher
    config: dict
    timeout_seconds: float


async def gather_context(
    invocations: list[FetcherInvocation],
    *,
    when: datetime,
) -> dict[str, Any]:
    """Run all fetchers concurrently, merge results into a context dict.

    Each fetcher gets its own timeout. Failures (exceptions or timeouts)
    land in ``context._errors.<type_id>`` so the receiving agent sees what
    fell over without losing the rest of the gather. One slow fetcher
    cannot block others — they all run in parallel with isolated timeouts.
    """
    results = await asyncio.gather(
        *[_run_one(inv, when) for inv in invocations],
        return_exceptions=False,  # all paths return tuples; never re-raise
    )
    context: dict[str, Any] = {}
    for is_error, key, payload in results:
        if is_error:
            errors = context.setdefault("_errors", {})
            errors[key] = payload
        else:
            _merge_into_namespace(context, key, payload)
    return context


async def _run_one(inv: FetcherInvocation, when: datetime) -> tuple[bool, str, dict]:
    """Execute one fetcher; never re-raise.

    Returns ``(is_error, key, payload)``. On success ``key`` is the
    fetcher's namespace (dot-separated path). On failure ``key`` is the
    fetcher's ``type_id``, kept literal so dots in the type_id don't
    create nested error dicts.
    """
    try:
        payload = await asyncio.wait_for(
            inv.fetcher.fetch(inv.config, when),
            timeout=inv.timeout_seconds,
        )
        return False, inv.fetcher.namespace, payload
    except TimeoutError:
        return (
            True,
            inv.fetcher.type_id,
            {
                "error": f"timeout after {inv.timeout_seconds}s",
                "type": "TimeoutError",
            },
        )
    except Exception as exc:
        return (
            True,
            inv.fetcher.type_id,
            {
                "error": str(exc),
                "type": type(exc).__name__,
            },
        )


def _merge_into_namespace(context: dict, namespace: str, payload: dict) -> None:
    """Merge ``payload`` into ``context`` at ``namespace`` (dot-separated path).

    ``namespace="streaks.eod_log"`` creates nested dicts. Existing keys at
    the leaf are overwritten — last write wins (intentional; multiple
    fetchers contributing to the same leaf is a config error caught at
    fetcher-loader time).
    """
    parts = namespace.split(".")
    cursor = context
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = payload
