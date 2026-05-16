"""Sanity test for FakeMessage/FakeChannel attachment roundtrip (#64)."""

from __future__ import annotations

import discord
import pytest
from agent_core_discord.testing.fakes import FakeChannel


@pytest.mark.asyncio
async def test_fake_message_records_attachments_from_send_call(tmp_path):
    """FakeChannel.send(files=[...]) returns a FakeMessage whose
    .attachments list reflects the inputs with .filename derived from
    each discord.File's filename attribute.

    Without this fake-side modeling, handler-level tests in Phase 3
    are toothless — they can't assert what reached Discord.
    """
    ch = FakeChannel(id="100")
    f1 = tmp_path / "alpha.pdf"
    f1.write_bytes(b"a" * 16)
    f2 = tmp_path / "beta.pdf"
    f2.write_bytes(b"b" * 16)
    discord_files = [discord.File(str(f1)), discord.File(str(f2))]
    try:
        msg = await ch.send(content="hi", files=discord_files)
        assert len(msg.attachments) == 2
        assert [a.filename for a in msg.attachments] == ["alpha.pdf", "beta.pdf"]
    finally:
        for df in discord_files:
            df.close()


from agent_core_discord.testing.fakes import FakeAttachment


def test_fake_attachment_carries_content_type_and_size():
    a = FakeAttachment(
        filename="pic.png",
        url="https://cdn.discordapp.com/x/pic.png",
        content_type="image/png",
        size=2048,
    )
    assert a.filename == "pic.png"
    assert a.url == "https://cdn.discordapp.com/x/pic.png"
    assert a.content_type == "image/png"
    assert a.size == 2048


def test_fake_attachment_defaults_match_real_optionality():
    # content_type can be absent on real attachments; size defaults to 0.
    a = FakeAttachment(filename="f.bin")
    assert a.url == ""
    assert a.content_type is None
    assert a.size == 0
