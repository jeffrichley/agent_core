"""discord_embed — render brief sections as Discord embeds, publish via DiscordEndpoint.

Strategy
--------
This destination does NOT call discord.py directly. It serializes each
section into an embed dict, attaches the list to a TextMessage envelope's
``metadata.discord.embeds``, and publishes the envelope to a configured
DiscordEndpoint by name. DiscordEndpoint is responsible for deserializing
those dicts into ``discord.Embed`` objects and calling ``channel.send(
embeds=...)``. Keeping the bus-side payload as plain JSON-friendly dicts
means the briefs package never imports ``discord.py``.

Config
------
- ``channel_id`` (str, required): destination Discord channel for the brief.
- ``discord_endpoint_name`` (str, optional, default ``"discord"``): the bus
  name of the DiscordEndpoint to publish through.

Footer convention
-----------------
A footer is auto-injected on the LAST embed only:

    ``{playbook.brief_type} • {when.isoformat()}``

The agent's name is intentionally omitted from the footer text — the
publishing endpoint stamps ``from_`` on the envelope already, so duplicating
it in the visible embed footer is noise, and ``BusHandle._endpoint_name``
isn't part of the public surface.

Failure semantics
-----------------
``Destination.deliver`` is best-effort per the brief framework spec — a bus
publish failure must not propagate into the orchestrator. Publish errors
land in the returned ``DeliveryResult(success=False, error=...)``.

Success semantics
-----------------
``DeliveryResult.success=True`` here means the envelope was successfully
*enqueued* for delivery to the DiscordEndpoint. The actual Discord API
call happens asynchronously inside the endpoint; failures there (rate
limits, malformed embed, channel not found) surface as Acknowledgments
on the bus, NOT on the ``DeliveryResult`` returned here.

``DeliveryResult.ref`` is the bus envelope id, NOT the Discord message
id. Audit / replay paths that need the actual message id should
correlate via ``envelope.correlation_id`` to the eventual
Acknowledgment from the DiscordEndpoint, whose payload note carries
the Discord message id(s) once the API call lands.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agent_core.bus.envelope import Envelope, TextMessagePayload
from agent_core_briefs.protocol import DeliveryResult, PlaybookRef

if TYPE_CHECKING:
    from agent_core.bus.handle import BusHandle


class DiscordEmbedDestination:
    """Render brief sections as Discord embeds, post via DiscordEndpoint.

    Config:
    - ``channel_id`` (str): destination channel for the brief
    - ``discord_endpoint_name`` (str): bus name of the DiscordEndpoint to
      publish through; default ``"discord"``
    """

    type_id = "discord_embed"

    async def deliver(
        self,
        sections: list[dict],
        playbook: PlaybookRef,
        scope: str | None,
        when: datetime,
        config: dict,
        bus_handle: BusHandle,
    ) -> DeliveryResult:
        """Build embed dicts, publish a TextMessage envelope to the endpoint.

        Returns ``DeliveryResult(success=True, ref=None)`` for an empty
        ``sections`` list (nothing to deliver, not an error). Any publish
        exception is captured into ``DeliveryResult(success=False,
        error=...)`` rather than propagating.
        """
        if not sections:
            return DeliveryResult(success=True, ref=None)

        try:
            channel_id = str(config["channel_id"])
        except KeyError:
            # Symmetric with the publish-failure path: surface config
            # errors as a DeliveryResult instead of propagating, so a
            # misconfigured destination doesn't crash the orchestrator.
            return DeliveryResult(
                success=False,
                error="discord_embed: config missing 'channel_id'",
            )
        target_name = config.get("discord_endpoint_name", "discord")

        embeds: list[dict[str, Any]] = [
            self._render_section_to_embed_dict(section) for section in sections
        ]
        # Footer goes on the LAST embed only — visually marks the end of
        # the brief without repeating on every section.
        embeds[-1]["footer"] = {
            "text": f"{playbook.brief_type} • {when.isoformat()}",
        }

        envelope = Envelope(
            id=uuid.uuid4().hex,
            correlation_id=uuid.uuid4().hex,
            to=target_name,
            kind="TextMessage",
            payload=TextMessagePayload(text=""),
            metadata={
                "discord": {
                    "channel_id": channel_id,
                    "embeds": embeds,
                },
            },
            created_at=when,
        )
        try:
            await bus_handle.publish(envelope)
        except Exception as exc:
            # Best-effort delivery per the brief framework spec — a publish
            # failure must NOT propagate into the orchestrator. Capture the
            # exception text into the DeliveryResult and let the caller
            # decide what to do with the partial outcome.
            return DeliveryResult(
                success=False,
                error=f"discord_embed: publish failed: {exc}",
            )
        return DeliveryResult(success=True, ref=envelope.id)

    @staticmethod
    def _render_section_to_embed_dict(section: dict) -> dict[str, Any]:
        """Convert a filled section dict to a serializable embed dict.

        Section dict shape (as built by the agent via ``tools.py``)::

            {
                "section_id": "greeting",
                "title": "🌅 Morning",
                "color": 15548997,  # int decimal, may be None
                "fields": [{"name": "Today", "value": "...", "inline": False}, ...],
            }

        The returned dict is a minimal representation that
        ``DiscordEndpoint`` knows how to lift into a ``discord.Embed``.
        ``color`` is preserved as int decimal (or None to let discord.py
        pick a default).
        """
        raw_color = section.get("color")
        color: int | None
        if raw_color is None:
            color = None
        else:
            color = int(raw_color)
        return {
            "title": section.get("title", ""),
            "color": color,
            "fields": [
                {
                    "name": f["name"],
                    "value": f["value"],
                    "inline": bool(f.get("inline", False)),
                }
                for f in section.get("fields", [])
            ],
        }
