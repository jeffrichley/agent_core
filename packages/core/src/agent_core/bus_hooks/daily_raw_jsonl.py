"""``BusHook`` that writes every published envelope to ``daily/<date>.jsonl``.

Cutover #04: bus traffic (Discord, scheduler, relay, agent-to-agent) lands
in a single bus-owned daily JSONL. Each agent's reflection job filters this
log to its perspective via ``agent_core.bus_log.iter_for_agent``.

Registered only at ``pre_publish`` so each logical publish is logged once
(redelivery does not re-run pre_publish).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from agent_core.bus.envelope import Envelope
from agent_core.bus_log import writer as _writer

log = logging.getLogger(__name__)


_DEFAULT_SKIP_KINDS: frozenset[str] = frozenset({"Acknowledgment", "Progress", "Cancellation"})


class DailyRawJsonlHook:
    """Append every published envelope to a dated daemon-owned JSONL.

    Configuration (once registered as ``builtin.daily_raw_jsonl`` in
    Task 8 of the bus log pipeline plan, via ``agent_core.yaml``
    ``bus_hooks.pre_publish`` block):

    .. code-block:: yaml

        bus_hooks:
          pre_publish:
            - type: builtin.daily_raw_jsonl
              params:
                log_root: "~/.agent-core/bus/raw"  # default
                timezone: "US/Eastern"              # default
                skip_kinds: ["Acknowledgment", "Progress", "Cancellation"]
    """

    def __init__(
        self,
        log_root: str | None = None,
        *,
        timezone: str = "US/Eastern",
        skip_kinds: list[str] | None = None,
    ) -> None:
        self._log_root = Path(log_root).expanduser() if log_root else _writer.default_log_root()
        self._timezone = timezone
        self._skip_kinds = (
            _DEFAULT_SKIP_KINDS if skip_kinds is None else frozenset(skip_kinds)
        )

    async def execute(
        self,
        stage: Literal["pre_publish", "pre_deliver"],
        envelope: Envelope,
        params: dict,
    ) -> Envelope | None:
        # Only log at pre_publish; pre_deliver fires per delivery attempt
        # and would duplicate on redelivery.
        if stage != "pre_publish":
            return envelope
        if envelope.kind in self._skip_kinds:
            return envelope
        path = _writer.daily_path(self._log_root, timezone=self._timezone, when=envelope.created_at)
        try:
            _writer.append_envelope_jsonl(path, envelope)
        except OSError:
            log.exception("daily_raw_jsonl: failed to append to %s", path)
        return envelope
