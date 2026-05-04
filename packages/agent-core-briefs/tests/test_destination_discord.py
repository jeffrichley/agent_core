"""Tests for ``DiscordEmbedDestination``.

The destination is bus-mediated — it doesn't import discord.py directly.
Tests use a recording bus handle stub that captures published envelopes
so we can assert the exact metadata shape DiscordEndpoint will deserialize.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.destinations.discord_embed import DiscordEmbedDestination
from agent_core_briefs.protocol import Destination, PlaybookRef

from agent_core.bus.envelope import Envelope, TextMessagePayload


class _RecordingHandle:
    """Stub BusHandle that records publish + ack calls.

    Mirrors the shape of the real ``BusHandle`` enough for the destination
    contract: ``async def publish(envelope, to=None)``. The destination
    only uses ``publish``; ``ack``/``nack``/``endpoints`` are present to
    satisfy structural duck-typing if anything tightens up later.
    """

    def __init__(self) -> None:
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...
    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self):
        return []


class _BoomHandle(_RecordingHandle):
    """Bus handle whose publish always blows up — used for failure path."""

    async def publish(self, envelope: Envelope, to=None) -> None:
        raise RuntimeError("simulated bus publish failure")


def _playbook(tmp_path: Path | None = None, brief_type: str = "morning_brief") -> PlaybookRef:
    return PlaybookRef(
        brief_type=brief_type,
        path=(tmp_path or Path(".")) / f"{brief_type}.md",
        sections_required=["greeting"],
        sections_optional=[],
    )


def _section(
    *,
    section_id: str = "greeting",
    title: str = "🌅 Morning",
    color: int | None = 15548997,
    fields: list[dict] | None = None,
) -> dict:
    return {
        "section_id": section_id,
        "title": title,
        "color": color,
        "fields": fields if fields is not None else [{"name": "Today", "value": "Sunny"}],
    }


def test_destination_satisfies_runtime_protocol() -> None:
    """Sanity check — the new bus_handle parameter keeps the protocol shape."""
    assert isinstance(DiscordEmbedDestination(), Destination)


@pytest.mark.asyncio
async def test_deliver_publishes_text_message_with_embeds_in_metadata(tmp_path: Path) -> None:
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    sections = [
        _section(section_id="a", title="A", color=1, fields=[{"name": "n1", "value": "v1"}]),
        _section(section_id="b", title="B", color=2, fields=[{"name": "n2", "value": "v2"}]),
        _section(section_id="c", title="C", color=3, fields=[{"name": "n3", "value": "v3"}]),
    ]
    when = datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC)

    result = await dest.deliver(
        sections=sections,
        playbook=_playbook(tmp_path),
        scope=None,
        when=when,
        config={"channel_id": "12345"},
        bus_handle=handle,
    )

    assert result.success is True
    assert len(handle.published) == 1
    env = handle.published[0]
    assert env.kind == "TextMessage"
    assert isinstance(env.payload, TextMessagePayload)
    assert env.to == "discord"  # default endpoint name
    discord_meta = env.metadata["discord"]
    assert discord_meta["channel_id"] == "12345"
    assert len(discord_meta["embeds"]) == 3
    titles = [e["title"] for e in discord_meta["embeds"]]
    assert titles == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_section_titles_become_embed_titles(tmp_path: Path) -> None:
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    sections = [
        _section(title="🌅 Morning"),
        _section(section_id="cal", title="📅 Calendar"),
    ]
    await dest.deliver(
        sections=sections,
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    embeds = handle.published[0].metadata["discord"]["embeds"]
    assert [e["title"] for e in embeds] == ["🌅 Morning", "📅 Calendar"]


@pytest.mark.asyncio
async def test_section_resolved_color_becomes_embed_color(tmp_path: Path) -> None:
    """Int decimal colors round-trip exactly into the embed dict."""
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    sections = [
        _section(section_id="a", color=15548997),
        _section(section_id="b", color=255),
        _section(section_id="c", color=None),  # defensive: pre-T7 unresolved
    ]
    await dest.deliver(
        sections=sections,
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    embeds = handle.published[0].metadata["discord"]["embeds"]
    assert [e["color"] for e in embeds] == [15548997, 255, None]


@pytest.mark.asyncio
async def test_section_fields_become_embed_fields(tmp_path: Path) -> None:
    """Field name/value pairs round-trip; ``inline`` defaults to False."""
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    sections = [
        _section(
            fields=[
                {"name": "Today", "value": "Sunny day"},
                {"name": "Traffic", "value": "Low", "inline": True},
            ],
        ),
    ]
    await dest.deliver(
        sections=sections,
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    embed = handle.published[0].metadata["discord"]["embeds"][0]
    assert embed["fields"] == [
        {"name": "Today", "value": "Sunny day", "inline": False},
        {"name": "Traffic", "value": "Low", "inline": True},
    ]


@pytest.mark.asyncio
async def test_footer_appears_on_last_embed_only(tmp_path: Path) -> None:
    """Footer is set on the LAST embed only; earlier embeds have no footer."""
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    when = datetime(2026, 5, 4, 7, 0, 0, tzinfo=UTC)
    sections = [_section(section_id="a"), _section(section_id="b"), _section(section_id="c")]
    await dest.deliver(
        sections=sections,
        playbook=_playbook(tmp_path, brief_type="morning_brief"),
        scope=None,
        when=when,
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    embeds = handle.published[0].metadata["discord"]["embeds"]
    # Only the last embed carries a footer.
    assert "footer" not in embeds[0]
    assert "footer" not in embeds[1]
    assert embeds[2]["footer"] == {"text": "morning_brief • 2026-05-04T07:00:00+00:00"}


@pytest.mark.asyncio
async def test_delivery_result_success_carries_envelope_id(tmp_path: Path) -> None:
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    result = await dest.deliver(
        sections=[_section()],
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    assert result.success is True
    assert result.error is None
    assert result.ref == handle.published[0].id
    assert result.ref  # non-empty


@pytest.mark.asyncio
async def test_publish_failure_returns_delivery_result_with_error(tmp_path: Path) -> None:
    """Bus publish raising must NOT propagate — best-effort delivery per spec."""
    dest = DiscordEmbedDestination()
    handle = _BoomHandle()
    result = await dest.deliver(
        sections=[_section()],
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    assert result.success is False
    assert result.ref is None
    assert result.error is not None
    assert "publish failed" in result.error
    assert "simulated bus publish failure" in result.error


@pytest.mark.asyncio
async def test_no_sections_publishes_no_envelope(tmp_path: Path) -> None:
    """Empty sections list short-circuits — no envelope, success, ref=None."""
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    result = await dest.deliver(
        sections=[],
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1"},
        bus_handle=handle,
    )
    assert result.success is True
    assert result.ref is None
    assert result.error is None
    assert handle.published == []


@pytest.mark.asyncio
async def test_custom_endpoint_name_routes_envelope(tmp_path: Path) -> None:
    """``discord_endpoint_name`` overrides the default ``discord`` target."""
    dest = DiscordEmbedDestination()
    handle = _RecordingHandle()
    await dest.deliver(
        sections=[_section()],
        playbook=_playbook(tmp_path),
        scope=None,
        when=datetime.now(UTC),
        config={"channel_id": "1", "discord_endpoint_name": "discord-pepper"},
        bus_handle=handle,
    )
    assert handle.published[0].to == "discord-pepper"
