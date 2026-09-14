"""Unit tests for ``send_retry`` helpers."""

from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock

import pytest
from agent_core_discord.send_retry import (
    DISCORD_SEND_MAX_ATTEMPTS,
    channel_send_with_retries,
    is_retryable_discord_send_error,
    retry_delay_seconds,
)


class _Exc(Exception):
    def __init__(self, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(str(status))
        if status is not None:
            self.status = status
        if retry_after is not None:
            self.retry_after = retry_after


def test_retryable_429_and_503() -> None:
    assert is_retryable_discord_send_error(_Exc(429)) is True
    assert is_retryable_discord_send_error(_Exc(503)) is True
    assert is_retryable_discord_send_error(_Exc(408)) is True


def test_not_retryable_4xx_client() -> None:
    assert is_retryable_discord_send_error(_Exc(400)) is False
    assert is_retryable_discord_send_error(_Exc(404)) is False


def test_retry_delay_uses_retry_after_when_present() -> None:
    d = retry_delay_seconds(_Exc(429, retry_after=2.5), attempt=0)
    assert d >= 2.5


@pytest.mark.asyncio
async def test_channel_send_with_retries_exhausts(monkeypatch):
    from agent_core_discord.send_retry import channel_send_with_retries

    calls = 0

    class _Ch:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            nonlocal calls
            calls += 1
            raise _Exc(429)

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    with pytest.raises(_Exc):
        await channel_send_with_retries(_Ch(), "hi", max_attempts=3)
    assert calls == 3


@pytest.mark.asyncio
async def test_channel_send_with_retries_succeeds_second_try(monkeypatch):
    from agent_core_discord.send_retry import channel_send_with_retries

    calls = 0

    class _Ch:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _Exc(429)
            return "ok"

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    out = await channel_send_with_retries(_Ch(), "hi", max_attempts=DISCORD_SEND_MAX_ATTEMPTS)
    assert out == "ok"
    assert calls == 2


class _FakeFile:
    """Mirrors ``discord.File``'s contract strictly, so this cannot pass here and
    fail in production.

    The library's own docstring: *"File objects are single use and are not meant
    to be reused in multiple abc.Messageable.send s."* ``MultipartParameters.__exit__``
    closes the file after every send, including one that raised, and a ``File``
    built from a path owns its handle. So the fake refuses exactly what the real
    object refuses: any use after close.
    """

    def __init__(self, payload: bytes = b"pdf-bytes") -> None:
        self._fp = io.BytesIO(payload)
        self.closed = False

    def reset(self, *, seek: bool | int = True) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        if seek:
            self._fp.seek(0)

    def read(self) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return self._fp.read()

    def close(self) -> None:
        self.closed = True
        self._fp.close()


class _UploadRecordingChannel:
    """A channel that uploads like discord.py does and fails the first attempt.

    Records the bytes each attempt actually managed to upload, which is the
    property under test: the message posting is not evidence the file did.
    """

    def __init__(self, fail_times: int = 1) -> None:
        self.uploads: list[bytes] = []
        self._fail_times = fail_times

    async def send(self, content: str | None = None, **kwargs: object) -> str:
        files = kwargs.get("files") or []
        assert isinstance(files, list)
        for f in files:
            f.reset(seek=len(self.uploads))  # discord.py http.py resets per try
            self.uploads.append(f.read())
        try:
            if len(self.uploads) <= self._fail_times:
                raise _Exc(429)
            return "ok"
        finally:
            for f in files:  # MultipartParameters.__exit__ — failure path too
                f.close()


@pytest.mark.asyncio
async def test_a_retried_send_uploads_the_file_again_not_a_consumed_handle(monkeypatch):
    """#594. The retry must upload real bytes, not an already-closed handle.

    Asserts what the second attempt *received*, not that the call returned — a
    send whose attachment silently vanished still returns a message id, which is
    exactly why this defect survived three weeks in production.
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    ch = _UploadRecordingChannel(fail_times=1)

    out = await channel_send_with_retries(
        ch,
        "here is the report",
        files_factory=lambda: [_FakeFile()],
        max_attempts=3,
    )

    assert out == "ok"
    assert len(ch.uploads) == 2, "expected exactly one retry"
    assert ch.uploads == [b"pdf-bytes", b"pdf-bytes"], (
        "the retry uploaded different bytes than the first attempt — "
        "an empty or partial second upload is the silent-drop defect"
    )


@pytest.mark.asyncio
async def test_a_retry_with_prebuilt_files_refuses_instead_of_uploading_a_consumed_handle(
    monkeypatch,
):
    """#594, the defect itself: a pre-built file list must never cross an attempt.

    This is the test that goes red on the ORIGINAL source, and it stays
    meaningful after the fix because it pins the *defect*, not the new keyword.
    Pre-fix the second attempt reaches a closed handle and dies with
    ``ValueError: I/O operation on closed file``; post-fix there is no second
    attempt at all and the caller sees the transport error that actually
    happened.

    Refusing to retry is deliberately WORSE service and BETTER behaviour: a
    retry here cannot deliver the attachment, so its only possible outcomes are
    a crash or a message that posts without its file. Failing on the real error
    is the one outcome that tells the truth.
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    ch = _UploadRecordingChannel(fail_times=1)
    the_file = _FakeFile()

    with pytest.raises(_Exc) as caught:
        await channel_send_with_retries(ch, "here is the report", files=[the_file], max_attempts=3)

    assert caught.value.status == 429, "the caller must see the transport error, not an I/O error"
    assert len(ch.uploads) == 1, "must not have attempted a second upload with a spent handle"
    assert the_file.closed is True


@pytest.mark.asyncio
async def test_passing_both_files_and_files_factory_is_refused() -> None:
    """Ambiguity is refused loudly rather than resolved by precedence.

    A caller who passes both has a pre-built list that cannot survive a retry;
    silently preferring one would leave the hazard in place under a fix's name.
    """

    class _Ch:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            raise AssertionError("send must not be reached")

    with pytest.raises(TypeError, match="files_factory"):
        await channel_send_with_retries(
            _Ch(), "hi", files=[_FakeFile()], files_factory=lambda: [_FakeFile()]
        )


@pytest.mark.asyncio
async def test_factory_built_files_are_released_when_send_raises_before_using_them(
    monkeypatch,
):
    """Every attempt's files are closed, even if ``send`` never touched them.

    A factory builds a fresh set per attempt, so a send that raises *before*
    taking ownership of the handles would leak one per attempt rather than one
    in total. The sender normally closes them itself; this pins the case where
    it never got the chance.
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    built: list[_FakeFile] = []

    def factory() -> list[_FakeFile]:
        made = _FakeFile()
        built.append(made)
        return [made]

    class _RaisesBeforeUpload:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            raise _Exc(429)  # never reads, never closes

    with pytest.raises(_Exc):
        await channel_send_with_retries(
            _RaisesBeforeUpload(), "hi", files_factory=factory, max_attempts=3
        )

    assert len(built) == 3, "one fresh set per attempt"
    assert all(f.closed for f in built), "a leaked handle per retry is the regression"


@pytest.mark.asyncio
async def test_a_failing_close_does_not_mask_the_transport_error(monkeypatch):
    """The caller must see why the send failed, not why cleanup failed.

    ``_release`` runs while an exception is already in flight. If a close error
    escaped it, the caller would get an unrelated ``OSError`` in place of the
    429 that actually happened — turning a diagnosable rate limit into a
    mystery, which is the same substitution the whole issue is about.
    """
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    class _UncloseableFile(_FakeFile):
        def close(self) -> None:
            raise OSError("cannot close")

    class _RaisesBeforeUpload:
        async def send(self, content: str | None = None, **kwargs: object) -> str:
            raise _Exc(429)

    with pytest.raises(_Exc) as caught:
        await channel_send_with_retries(
            _RaisesBeforeUpload(),
            "hi",
            files_factory=lambda: [_UncloseableFile()],
            max_attempts=2,
        )

    assert caught.value.status == 429
