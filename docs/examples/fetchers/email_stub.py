"""email_stub — placeholder ``email`` fetcher for the morning-brief example.

Demonstrates the shape an agent-side fetcher takes when the framework
doesn't ship a built-in for the data source. Email is genuinely
agent-specific (which client, which auth, which inbox shape), so the
framework intentionally does NOT ship an email built-in. This stub lets
the example playbook (``docs/examples/playbooks/morning-brief.md``)
parse and run end-to-end while authors build the real integration.

Swap with a real client when ready: keep ``type_id = "email_stub"`` (or
rename to your own type — anything not colliding with built-ins is
fine), keep the output shape, and rewrite ``fetch`` to talk to your
actual mail backend.

Output shape:
- ``urgent`` (list[dict]): each item ``{from, subject}``. The morning-brief
  playbook gates a dynamic embed color on ``len(email.urgent) > 0``,
  so emitting an empty list is the "all clear" state.
- ``unread`` (int): total unread count, for the section's text body.
"""

from __future__ import annotations

from datetime import datetime


class EmailStubFetcher:
    """Always reports zero urgent + zero unread.

    Replace ``fetch`` with your real implementation when you're ready.
    The example playbook only cares that ``email.urgent`` is iterable
    and ``email.unread`` is an int — both shapes are stable.
    """

    type_id = "email_stub"
    namespace = ""  # set per-invocation by the gather config

    async def fetch(self, config: dict, when: datetime) -> dict:
        return {"urgent": [], "unread": 0}
