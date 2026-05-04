"""Test-only fetcher returning a fixed calendar payload.

Used by ``test_e2e_morning_brief.py`` for the integration test. Lives
under ``tests/fixtures/`` so the orchestrator's ``fetcher_paths``
discovery picks it up alongside the framework's built-in fetchers
(``filesystem_read``, ``cli``).

The payload is intentionally small and deterministic so the e2e test
can assert the exact structure landed in ``context['calendar']``
after gather runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class FakeCalendarFetcher:
    """Return a fixed calendar dict regardless of config.

    Implements the ``Fetcher`` Protocol (``type_id``, ``namespace``,
    async ``fetch``). The class-level namespace is empty so the gather
    config supplies the per-invocation namespace, matching the
    convention used by the real built-in fetchers.
    """

    type_id = "test.fake_calendar"
    namespace = ""

    def __init__(self) -> None:
        # The orchestrator instantiates fetchers from the catalog at
        # request time; instances must be cheap to build with no args.
        self._payload: dict[str, Any] = {
            "events": [
                {"title": "Standup", "start": "09:00", "duration_min": 15},
                {"title": "Pairing on cutover #09", "start": "10:00", "duration_min": 90},
            ],
            "next_event_in_min": 30,
        }

    async def fetch(self, config: dict, when: datetime) -> dict:
        # ``when`` and ``config`` are intentionally unused: the test wants
        # a deterministic payload so the assertions can pin the exact
        # shape of ``context['calendar']`` that lands on the ComposeBrief.
        del config, when
        return dict(self._payload)
