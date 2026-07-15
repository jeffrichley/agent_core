"""Tests for voice memo auto-transcription in DiscordEndpoint (issue #155).

Covers: happy path, no-faster-whisper fallback, transcription exception,
too-long audio, non-audio unchanged, warm-model reuse,
transcribe_voice=False, and mixed typed-text + voice.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from agent_core.bus.envelope import EndpointInfo, Envelope, TextMessagePayload
from agent_core_discord.endpoint import DiscordEndpoint
from agent_core_discord.testing.fakes import (
    FakeAttachment,
    FakeChannel,
    FakeDiscordClient,
    FakeMessage,
    FakeUser,
)


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


class _Recording:
    def __init__(self):
        self.published: list[Envelope] = []

    async def publish(self, envelope: Envelope, to=None) -> None:
        self.published.append(envelope)

    async def ack(self, envelope_id: str) -> None: ...

    async def nack(self, envelope_id: str, requeue: bool = True) -> None: ...

    def endpoints(self) -> list[EndpointInfo]:
        return []

    def spawn(self, coro, *, name=None):
        import asyncio

        return asyncio.create_task(coro, name=name)


async def _start_endpoint(
    monkeypatch, **kwargs
) -> tuple[DiscordEndpoint, _Recording, FakeDiscordClient]:
    monkeypatch.setenv("X_TOK", "tok")
    handle = _Recording()
    fake = FakeDiscordClient()
    ep = DiscordEndpoint(
        name="discord-voice-test",
        target="agent-test",
        token_env="X_TOK",
        _client_factory=lambda **kw: fake,
        **kwargs,
    )
    await ep.start(handle)
    return ep, handle, fake


def _voice_msg(*, content: str = "", duration_secs: float | None = 30.0) -> FakeMessage:
    """Build a FakeMessage carrying a single audio/ogg attachment."""
    msg = FakeMessage(id="vm-1", channel_id="ch-1", content=content)
    msg.author = FakeUser(id="100", name="jeff", bot=False, display_name="Jeff")
    msg.guild = type("G", (), {"id": "guild-1"})()
    msg.channel = FakeChannel(id="ch-1")
    att = FakeAttachment(
        filename="voice-message.ogg",
        url="https://cdn.discordapp.com/voice-messages/123/456/voice-message.ogg",
        content_type="audio/ogg",
        size=4096,
        duration_secs=duration_secs,
    )
    msg.attachments = [att]
    return msg


def _make_faster_whisper_mock(
    transcription: str = "hello from voice",
) -> tuple[types.ModuleType, MagicMock, MagicMock]:
    """Return (module, model_instance_mock, model_class_mock) for faster_whisper."""
    mock_module = types.ModuleType("faster_whisper")
    mock_segment = MagicMock()
    mock_segment.text = f" {transcription} "
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], MagicMock())
    mock_model_class = MagicMock(return_value=mock_model)
    mock_module.WhisperModel = mock_model_class
    return mock_module, mock_model, mock_model_class


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_transcription_happy_path(monkeypatch, tmp_path):
    """Voice attachment is transcribed; payload.text = '[voice: ...]'; metadata carries
    'transcription' key with the text and 'local_path' is populated."""
    fw_module, _, _ = _make_faster_whisper_mock("hello from voice")
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = _voice_msg()
    msg.channel = fake.get_channel("ch-1")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert isinstance(env.payload, TextMessagePayload)
        assert env.payload.text == "[voice: hello from voice]"
        attachments = env.metadata["attachments"]
        assert len(attachments) == 1
        assert attachments[0]["transcription"] == "hello from voice"
        assert attachments[0]["local_path"] is not None
        assert "transcription_error" not in attachments[0]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_voice_no_faster_whisper(monkeypatch, tmp_path):
    """When faster-whisper is not installed, delivery continues with
    transcription_error='faster-whisper not installed' and no crash."""
    # faster_whisper is not installed in the test environment; don't inject a mock.
    # Ensure any leftover mock from a previous test is gone.
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = _voice_msg()
    msg.channel = fake.get_channel("ch-1")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert isinstance(env.payload, TextMessagePayload)
        attachments = env.metadata["attachments"]
        assert attachments[0]["transcription_error"] == "faster-whisper not installed"
        assert "transcription" not in attachments[0]
        # Message delivered — not dropped or blocked.
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_voice_transcription_exception(monkeypatch, tmp_path):
    """Transcription exception (not ImportError) → transcription_error set, delivery continues."""
    fw_module, mock_model, _ = _make_faster_whisper_mock()
    mock_model.transcribe.side_effect = RuntimeError("whisper internal error")
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = _voice_msg()
    msg.channel = fake.get_channel("ch-1")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        attachments = env.metadata["attachments"]
        assert "transcription_error" in attachments[0]
        assert "transcription" not in attachments[0]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_voice_too_long(monkeypatch, tmp_path):
    """Audio longer than transcribe_max_duration_secs → skip with error marker."""
    fw_module, _, _ = _make_faster_whisper_mock()
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = _voice_msg(duration_secs=400.0)  # exceeds default 300 s
    msg.channel = fake.get_channel("ch-1")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        attachments = env.metadata["attachments"]
        assert attachments[0]["transcription_error"] == "audio too long (400s)"
        assert "transcription" not in attachments[0]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_non_audio_attachment_unchanged(monkeypatch, tmp_path):
    """Non-audio attachments (PDFs, images) are not transcribed."""
    fw_module, mock_model, mock_model_class = _make_faster_whisper_mock()
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"pdfdata", "application/pdf"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = FakeMessage(id="pdf-1", channel_id="ch-1", content="see attached")
    msg.author = FakeUser(id="100", name="jeff", bot=False, display_name="Jeff")
    msg.guild = type("G", (), {"id": "guild-1"})()
    msg.channel = fake.get_channel("ch-1")
    msg.attachments = [
        FakeAttachment(
            filename="report.pdf",
            url="https://cdn.discordapp.com/x/report.pdf",
            content_type="application/pdf",
            size=2048,
        )
    ]

    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        attachments = env.metadata["attachments"]
        assert "transcription" not in attachments[0]
        assert "transcription_error" not in attachments[0]
        # WhisperModel never constructed for non-audio.
        assert mock_model_class.call_count == 0
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_warm_model_reuse(monkeypatch, tmp_path):
    """WhisperModel constructor called once; transcribe() called once per message."""
    fw_module, mock_model, mock_model_class = _make_faster_whisper_mock("hello")
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    try:
        for i in range(2):
            msg = FakeMessage(id=f"vm-{i}", channel_id="ch-1", content="")
            msg.author = FakeUser(id="100", name="jeff", bot=False, display_name="Jeff")
            msg.guild = type("G", (), {"id": "guild-1"})()
            msg.channel = fake.get_channel("ch-1")
            msg.attachments = [
                FakeAttachment(
                    filename=f"voice-{i}.ogg",
                    url=f"https://cdn.discordapp.com/voice-{i}.ogg",
                    content_type="audio/ogg",
                    size=4096,
                    duration_secs=10.0,
                )
            ]
            await fake.fire("on_message", msg)

        # Model constructed exactly once (warm-model reuse).
        assert mock_model_class.call_count == 1
        # transcribe() called once per message.
        assert mock_model.transcribe.call_count == 2
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_transcribe_voice_false(monkeypatch, tmp_path):
    """transcribe_voice=False → audio attachment present, no transcription attempted,
    'transcription' key absent from attachment metadata."""
    fw_module, mock_model, mock_model_class = _make_faster_whisper_mock("hello")
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch, transcribe_voice=False)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = _voice_msg()
    msg.channel = fake.get_channel("ch-1")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        attachments = env.metadata["attachments"]
        assert "transcription" not in attachments[0]
        assert "transcription_error" not in attachments[0]
        # WhisperModel never constructed when transcribe_voice=False.
        assert mock_model_class.call_count == 0
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_mixed_typed_text_and_voice(monkeypatch, tmp_path):
    """Message with content='hello' and voice attachment delivers
    payload.text == 'hello\\n[voice: <transcription>]'."""
    fw_module, _, _ = _make_faster_whisper_mock("voice content here")
    monkeypatch.setitem(sys.modules, "faster_whisper", fw_module)

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="ch-1"))

    async def fake_download(url):
        return b"audio-data", "audio/ogg"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    msg = _voice_msg(content="hello")
    msg.channel = fake.get_channel("ch-1")
    try:
        await fake.fire("on_message", msg)
        assert len(handle.published) == 1
        env = handle.published[0]
        assert env.payload.text == "hello\n[voice: voice content here]"
    finally:
        await ep.stop()
