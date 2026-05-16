# Discord Attachment Auto-Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-download Discord attachments at inbound, enrich `metadata["attachments"]` with a readable `local_path`, surface an attachment block in the channel `<inbox>` render, and reap old attachments with a daemon-side sweep.

**Architecture:** Daemon-side only, two packages. `agent-core-discord` downloads each attachment synchronously at inbound (reusing existing `_download_url`/`_safe_filename`/path-guard machinery via an extracted `_persist_attachment` helper) and runs a periodic retention sweep. `agent-core-channel` appends an attachment block to the TextMessage body in `render_envelope`. Bus core, MCP endpoint, wake notification, and the agent side are untouched.

**Tech Stack:** Python 3.12, pydantic v2 envelopes, `httpx` (already used by `_download_url`), `asyncio` background tasks, pytest + existing Discord fakes.

**Spec:** `docs/superpowers/specs/2026-05-15-discord-attachment-autodownload-design.md`
**Issue:** [#76](https://github.com/jeffrichley/agent_core/issues/76)
**Branch:** `feat/issue-76-attachment-autodownload` (spec already committed there; implementation lands on the same branch).

---

## File Structure

*Modified:*
- `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py` — `FakeAttachment` gains `content_type` + `size` (mirror real `discord.Attachment`).
- `packages/agent-core-discord/src/agent_core_discord/endpoint.py` — extract `_persist_attachment`; refactor `_download_attachments` to use it; auto-download + enrich at inbound; retention sweep + lifecycle wiring; new `__init__` params.
- `packages/agent-core-channel/src/agent_core_channel/rendering.py` — `_render_attachments_block` + `_humanize_bytes`; `_render_text_message_body` appends the block.

*Test files:*
- `packages/agent-core-discord/tests/test_fakes_attachments.py` — extend for new fake fields.
- `packages/agent-core-discord/tests/test_endpoint_inbound.py` — extend for auto-download behavior.
- `packages/agent-core-discord/tests/test_attachment_retention.py` — **new**, sweep unit tests.
- `packages/agent-core-channel/tests/test_rendering.py` — extend for the attachment block.

*Layering:* `_persist_attachment` is the single download+persist primitive (raises on failure). The MCP `download_attachments` tool keeps fail-loud `_ToolError` semantics; the inbound path catches per-attachment and records `download_error` (best-effort, message never lost). The renderer is defensive (never raises — a render exception would lose the whole envelope).

---

## Task 1: `FakeAttachment` mirrors real `content_type` + `size`

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py:93-102`
- Test: `packages/agent-core-discord/tests/test_fakes_attachments.py`

The inbound code reads `getattr(att, "content_type", ...)` and `getattr(att, "size", 0)`. Real `discord.Attachment` has both. Per the test-fakes-mirror-real discipline, the fake must carry them so inbound tests exercise the real shape.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent-core-discord/tests/test_fakes_attachments.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-discord/tests/test_fakes_attachments.py::test_fake_attachment_carries_content_type_and_size -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'content_type'`.

- [ ] **Step 3: Extend `FakeAttachment`**

Replace `packages/agent-core-discord/src/agent_core_discord/testing/fakes.py:93-102` with:

```python
class FakeAttachment:
    """Minimal stand-in for discord.Attachment on a fake message.

    Mirrors the real fields the endpoint reads: filename, url,
    content_type, size. Per the test-fakes-mirror-real-strictly
    discipline — the inbound path does getattr(att, "content_type") and
    getattr(att, "size"), so the fake must carry them.
    """

    def __init__(
        self,
        *,
        filename: str,
        url: str = "",
        content_type: str | None = None,
        size: int = 0,
    ):
        self.filename = filename
        self.url = url
        self.content_type = content_type
        self.size = size
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-discord/tests/test_fakes_attachments.py -v`
Expected: PASS (existing tests + 2 new).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/testing/fakes.py packages/agent-core-discord/tests/test_fakes_attachments.py
git commit -m "test(discord): FakeAttachment carries content_type + size (#76)"
```

---

## Task 2: Extract `_persist_attachment`; refactor `_download_attachments` to use it

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py:1469-1510`
- Test: `packages/agent-core-discord/tests/test_endpoint_inbound.py` (regression — existing `download_attachments` tests must stay green)

Pure refactor. Extract the per-URL persist body (safe filename → mkdir → traversal guard → dedup → download → write) into one helper both callers share. **No behavior change** to the `download_attachments` MCP tool.

- [ ] **Step 1: Write the failing test**

Append to `packages/agent-core-discord/tests/test_endpoint_inbound.py`:

```python
import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_persist_attachment_writes_file_and_returns_path(monkeypatch, tmp_path):
    from agent_core_discord.endpoint import DiscordEndpoint

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
    )

    async def fake_download(url):
        return b"PNGDATA", "image/png"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    path = await ep._persist_attachment(
        url="https://cdn.discordapp.com/a/pic.png", subdir="env-abc"
    )
    assert isinstance(path, Path)
    assert path.read_bytes() == b"PNGDATA"
    assert path.parent == (tmp_path / "env-abc").resolve()
    assert path.name == "pic.png"


@pytest.mark.asyncio
async def test_persist_attachment_dedups_collision(monkeypatch, tmp_path):
    from agent_core_discord.endpoint import DiscordEndpoint

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
    )

    async def fake_download(url):
        return b"DATA", "application/octet-stream"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    p1 = await ep._persist_attachment(
        url="https://cdn.discordapp.com/a/f.bin", subdir="env-1"
    )
    p2 = await ep._persist_attachment(
        url="https://cdn.discordapp.com/b/f.bin", subdir="env-1"
    )
    assert p1 != p2
    assert p1.read_bytes() == b"DATA"
    assert p2.read_bytes() == b"DATA"


@pytest.mark.asyncio
async def test_persist_attachment_raises_on_download_failure(monkeypatch, tmp_path):
    from agent_core_discord.endpoint import DiscordEndpoint

    ep = DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
    )

    async def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(ep, "_download_url", boom)

    with pytest.raises(Exception):
        await ep._persist_attachment(
            url="https://cdn.discordapp.com/a/x.png", subdir="env-2"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_inbound.py::test_persist_attachment_writes_file_and_returns_path -v`
Expected: FAIL with `AttributeError: 'DiscordEndpoint' object has no attribute '_persist_attachment'`.

- [ ] **Step 3: Add `_persist_attachment` and refactor `_download_attachments`**

Replace `packages/agent-core-discord/src/agent_core_discord/endpoint.py:1469-1510` (the current `_download_attachments` method) with:

```python
    async def _persist_attachment(self, *, url: str, subdir: str) -> Path:
        """Download one URL into <attachments_dir>/<subdir>/ and return the
        resolved path. Raises on download failure or unsafe path.

        Shared by the download_attachments MCP tool (subdir=message_id) and
        the inbound auto-download path (subdir=envelope_id).
        """
        target_dir = self.attachments_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_resolved = target_dir.resolve()
        filename = _safe_filename(url)
        path = (target_dir / filename).resolve()
        try:
            path.relative_to(target_resolved)
        except ValueError as exc:
            raise _ToolError(f"refused unsafe path for {url!r}") from exc
        # De-dup so two URLs ending in the same name don't silently overwrite.
        if path.exists():
            stem, suffix = path.stem, path.suffix
            path = (target_dir / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}").resolve()
            try:
                path.relative_to(target_resolved)
            except ValueError as exc:
                raise _ToolError(f"refused unsafe dedup path for {url!r}") from exc
        data, _content_type = await self._download_url(url)
        path.write_bytes(data)
        return path

    async def _download_attachments(self, args: _DownloadAttachmentsArgs) -> dict:
        if not args.attachment_urls:
            return {"saved": []}
        saved: list[dict] = []
        for url in args.attachment_urls:
            try:
                path = await self._persist_attachment(
                    url=url, subdir=args.message_id
                )
            except _ToolError:
                raise
            except Exception as exc:
                raise _ToolError(f"download failed for {url}: {exc}") from exc
            data = path.read_bytes()
            saved.append(
                {
                    "filename": path.name,
                    "path": str(path),
                    "content_type": "",
                    "size_bytes": len(data),
                }
            )
        return {"saved": saved}
```

Note the one intentional behavior delta documented here: the MCP tool's `saved[].content_type` was previously the HTTP response Content-Type; it is now `""`. Reason: `_persist_attachment` returns the path, not the content-type, to keep one clean primitive. Agents needing the type already have `metadata["attachments"][].content_type` from the inbound envelope (Discord's declared type), which is more reliable than the CDN response header. If an existing test asserts the old non-empty `content_type` from `download_attachments`, update that assertion to `""` and add a one-line comment pointing at this plan task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -v`
Then the full discord suite to catch regressions in the existing `download_attachments` tool tests:
Run: `uv run pytest packages/agent-core-discord/ -v`
Expected: PASS. If an existing `download_attachments` test fails ONLY on `content_type == "<something>"` vs `""`, apply the assertion update described in Step 3 and re-run. Any other failure is a real regression — stop and report.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_inbound.py
git commit -m "refactor(discord): extract _persist_attachment shared by tool + inbound (#76)"
```

---

## Task 3: Auto-download at inbound + enrich metadata

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py:908-947`
- Test: `packages/agent-core-discord/tests/test_endpoint_inbound.py`

Mint the envelope id first, download each attachment into `<env_id>/`, enrich each metadata dict, then construct the envelope. This keeps enrichment *before* `Envelope(...)` so we never fight pydantic's copy semantics, and download happens before any inbound-state mutation so no rollback widening is needed.

- [ ] **Step 1: Write the failing tests**

Append to `packages/agent-core-discord/tests/test_endpoint_inbound.py`:

```python
@pytest.mark.asyncio
async def test_inbound_autodownloads_and_enriches_metadata(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeChannel, FakeAttachment

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    async def fake_download(url):
        return b"IMGBYTES", "image/png"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    att = FakeAttachment(
        filename="pic.png",
        url="https://cdn.discordapp.com/a/pic.png?ex=deadbeef",
        content_type="image/png",
        size=8,
    )
    msg = _msg(id="m-att", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        a0 = env.metadata["attachments"][0]
        # Existing fields preserved.
        assert a0["filename"] == "pic.png"
        assert a0["url"] == "https://cdn.discordapp.com/a/pic.png?ex=deadbeef"
        assert a0["content_type"] == "image/png"
        assert a0["size_bytes"] == 8
        # New fields.
        assert a0["local_path"] is not None
        assert "download_error" not in a0
        # local_path is a real readable file with the right bytes.
        assert Path(a0["local_path"]).read_bytes() == b"IMGBYTES"
        # Grouped under the envelope id.
        assert env.id in a0["local_path"]
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_download_failure_degrades_url_only(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeChannel, FakeAttachment

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    async def boom(url):
        raise RuntimeError("timeout")

    monkeypatch.setattr(ep, "_download_url", boom)

    att = FakeAttachment(
        filename="clip.mov",
        url="https://cdn.discordapp.com/a/clip.mov",
        content_type="video/quicktime",
        size=999,
    )
    msg = _msg(id="m-fail", content="did you see it?", attachments=[att])
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        # Envelope STILL published — text never lost.
        env = handle.published[0]
        assert env.payload.text == "did you see it?"
        a0 = env.metadata["attachments"][0]
        assert a0["local_path"] is None
        assert "timeout" in a0["download_error"]
        # CDN url preserved for debugging.
        assert a0["url"] == "https://cdn.discordapp.com/a/clip.mov"
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_inbound_multi_attachment_distinct_paths(monkeypatch, tmp_path):
    from agent_core_discord.testing.fakes import FakeChannel, FakeAttachment

    ep, handle, fake = await _start_endpoint(monkeypatch)
    ep.attachments_dir = tmp_path
    fake.add_channel(FakeChannel(id="200"))

    payloads = {
        "https://cdn.discordapp.com/a/one.png": b"ONE",
        "https://cdn.discordapp.com/a/two.png": b"TWO",
        "https://cdn.discordapp.com/a/three.png": b"THREE",
    }

    async def fake_download(url):
        return payloads[url], "image/png"

    monkeypatch.setattr(ep, "_download_url", fake_download)

    atts = [
        FakeAttachment(filename=f"{n}.png", url=u, content_type="image/png", size=len(b))
        for (u, b), n in zip(payloads.items(), ["one", "two", "three"])
    ]
    msg = _msg(id="m-multi", attachments=atts)
    msg.channel = fake.get_channel("200")
    try:
        await fake.fire("on_message", msg)
        env = handle.published[0]
        paths = [a["local_path"] for a in env.metadata["attachments"]]
        assert len(paths) == 3
        assert len(set(paths)) == 3
        assert Path(paths[0]).read_bytes() == b"ONE"
        assert Path(paths[2]).read_bytes() == b"THREE"
    finally:
        await ep.stop()
```

If `_start_endpoint` / `_msg` helpers are not already in this test module, use the same construction the existing `test_on_message_attachments_metadata` test uses (it is already in this file — mirror its setup exactly; do not invent a new harness).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_inbound.py::test_inbound_autodownloads_and_enriches_metadata -v`
Expected: FAIL — `a0["local_path"]` KeyError (no enrichment yet).

- [ ] **Step 3: Rewrite the inbound block to mint id, download, enrich, then construct**

Replace `packages/agent-core-discord/src/agent_core_discord/endpoint.py:908-947` (from the `# 5. Collect attachment metadata` comment through the `env = Envelope(...)` construction) with:

```python
            # 5. Collect attachment metadata.
            attachments: list[dict[str, Any]] = []
            for att in getattr(message, "attachments", []) or []:
                attachments.append(
                    {
                        "filename": att.filename,
                        "url": att.url,
                        "content_type": getattr(att, "content_type", None) or "unknown",
                        "size_bytes": int(getattr(att, "size", 0)),
                    }
                )

            # 6. Build and publish the envelope.
            #    Sigil-prefix urgency: '!' -> red, '?' -> yellow, plain -> green.
            #    The sigil is stripped from the published payload text. See issue #38.
            urgency, text = parse_sigil(message.content or "")

            # Mint the envelope id up front so attachment files can be grouped
            # under <attachments_dir>/<envelope_id>/ and enrichment happens
            # before Envelope(...) construction (avoids pydantic copy aliasing).
            env_id = uuid.uuid4().hex

            # 5b. Auto-download each attachment (best-effort, per-attachment).
            #     Failure never blocks or loses the text message: the dict
            #     keeps its CDN url and gains a download_error marker.
            for entry in attachments:
                try:
                    local = await self._persist_attachment(
                        url=entry["url"], subdir=env_id
                    )
                    entry["local_path"] = str(local)
                except Exception as exc:  # noqa: BLE001 — best-effort by design
                    entry["local_path"] = None
                    entry["download_error"] = f"{type(exc).__name__}: {exc}"
                    log.warning(
                        "discord(%s): attachment download failed for %s — %s",
                        self.name,
                        entry.get("filename"),
                        exc,
                    )

            metadata: dict[str, Any] = {
                "discord": {
                    "channel_id": str(message.channel.id),
                    "message_id": str(message.id),
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "author_id": str(message.author.id),
                    "author_display_name": getattr(message.author, "display_name", "") or "",
                    "is_dm": is_dm,
                },
            }
            if attachments:
                metadata["attachments"] = attachments

            env = Envelope(
                id=env_id,
                correlation_id=uuid.uuid4().hex,
                to=self.target,
                kind="TextMessage",
                payload=TextMessagePayload(text=text),
                metadata=metadata,
                urgency=urgency,
                created_at=datetime.now(UTC),
            )
```

Everything from `assert self._handle is not None` (old line 948) onward is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-discord/tests/test_endpoint_inbound.py -v`
Expected: PASS — the 3 new tests plus the existing `test_on_message_attachments_metadata` (which asserts the base 4 fields; those are preserved, so it still passes).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_endpoint_inbound.py
git commit -m "feat(discord): auto-download attachments at inbound, enrich metadata (#76)"
```

---

## Task 4: Renderer attachment block

**Files:**
- Modify: `packages/agent-core-channel/src/agent_core_channel/rendering.py:44-46`
- Test: `packages/agent-core-channel/tests/test_rendering.py`

Append an attachment block to the TextMessage body, sourced from `metadata["attachments"]`. Internally defensive — never raises (a raise would route the whole envelope to fallback rendering, losing the text body).

- [ ] **Step 1: Write the failing tests**

Append to `packages/agent-core-channel/tests/test_rendering.py`:

```python
from agent_core_channel.rendering import render_envelope, _humanize_bytes


def test_humanize_bytes_units():
    assert _humanize_bytes(0) == "0 B"
    assert _humanize_bytes(512) == "512 B"
    assert _humanize_bytes(835 * 1024) == "835 KB"
    assert _humanize_bytes(22 * 1024 * 1024) == "22 MB"


def _text_env(attachments):
    return {
        "id": "env1",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
        "payload": {"kind": "TextMessage", "text": "did you see it?"},
        "metadata": {"attachments": attachments},
    }


def test_render_attachment_success_line():
    env = _text_env(
        [
            {
                "filename": "IMG.png",
                "url": "https://cdn/x",
                "content_type": "image/png",
                "size_bytes": 835 * 1024,
                "local_path": r"C:\Users\jeffr\.agent-core\attachments\discord-pepper\env1\IMG.png",
            }
        ]
    )
    out = render_envelope(env)
    assert "did you see it?" in out
    assert "[attachment: IMG.png (image/png, 835 KB) " in out
    assert r"env1\IMG.png" in out
    assert "download failed" not in out


def test_render_attachment_failure_line():
    env = _text_env(
        [
            {
                "filename": "clip.mov",
                "url": "https://cdn/clip.mov",
                "content_type": "video/quicktime",
                "size_bytes": 22 * 1024 * 1024,
                "local_path": None,
                "download_error": "RuntimeError: timeout",
            }
        ]
    )
    out = render_envelope(env)
    assert "clip.mov" in out
    assert "download failed" in out
    assert "https://cdn/clip.mov" in out


def test_render_multi_attachment_order():
    env = _text_env(
        [
            {"filename": "a.png", "url": "u1", "content_type": "image/png",
             "size_bytes": 10, "local_path": "/p/a.png"},
            {"filename": "b.png", "url": "u2", "content_type": "image/png",
             "size_bytes": 20, "local_path": "/p/b.png"},
        ]
    )
    out = render_envelope(env)
    assert out.index("a.png") < out.index("b.png")


def test_render_malformed_attachments_degrades_not_raises():
    env = {
        "id": "env2",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
        "payload": {"kind": "TextMessage", "text": "hello"},
        "metadata": {"attachments": "not-a-list"},
    }
    out = render_envelope(env)  # must not raise
    assert "hello" in out
    assert "render='fallback'" not in out  # text body preserved, not fallback


def test_render_no_attachments_body_unchanged():
    env = {
        "id": "env3",
        "kind": "TextMessage",
        "from": "discord-pepper",
        "urgency": "green",
        "payload": {"kind": "TextMessage", "text": "plain"},
        "metadata": {},
    }
    out = render_envelope(env)
    assert out == "<inbox kind='TextMessage' from='discord-pepper' urgency='green' envelope_id='env3'>\nplain\n</inbox>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py::test_humanize_bytes_units -v`
Expected: FAIL with `ImportError: cannot import name '_humanize_bytes'`.

- [ ] **Step 3: Add the humanizer + block helper; wire into the text body renderer**

In `packages/agent-core-channel/src/agent_core_channel/rendering.py`, add these two functions immediately above `_render_text_message_body` (currently at line 44):

```python
def _humanize_bytes(n: int) -> str:
    """Powers of 1024, two significant figures, suffix B/KB/MB/GB."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0 B"
    if n < 1024:
        return f"{n} B"
    units = ["KB", "MB", "GB"]
    size = float(n)
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == "GB":
            if size >= 100:
                return f"{int(round(size))} {unit}"
            return f"{size:.1f}".rstrip("0").rstrip(".") + f" {unit}"
    return f"{int(round(size))} GB"


def _render_attachments_block(env: dict) -> str:
    """Return an escaped attachment block for an envelope, or '' if none.

    Never raises: a malformed metadata['attachments'] degrades to '' (or
    skips bad entries) rather than routing the whole envelope to fallback
    rendering and losing the text body.
    """
    try:
        metadata = env.get("metadata") or {}
        if not isinstance(metadata, dict):
            return ""
        atts = metadata.get("attachments")
        if not isinstance(atts, list) or not atts:
            return ""
        lines: list[str] = []
        for a in atts:
            if not isinstance(a, dict):
                continue
            filename = str(a.get("filename", "?"))
            ctype = str(a.get("content_type", "unknown"))
            size = _humanize_bytes(a.get("size_bytes", 0))
            local_path = a.get("local_path")
            if local_path:
                lines.append(
                    f"[attachment: {filename} ({ctype}, {size}) "
                    f"→ {local_path}]"
                )
            else:
                err = str(a.get("download_error", "unknown error"))
                url = str(a.get("url", ""))
                lines.append(
                    f"[attachment: {filename} ({ctype}, {size}) "
                    f"— download failed ({err}); "
                    f"CDN url may be expired: {url}]"
                )
        if not lines:
            return ""
        return "\n\n" + encode_body("\n".join(lines))
    except Exception:
        return ""
```

Then replace `_render_text_message_body` (lines 44-46) with:

```python
def _render_text_message_body(env: dict) -> str:
    text = env.get("payload", {}).get("text", "")
    return encode_body(str(text)) + _render_attachments_block(env)
```

`encode_body` is already imported/defined at the top of this module; `_render_attachments_block` escapes its own joined lines, and the text body is escaped separately — each segment is escaped exactly once.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-channel/tests/test_rendering.py -v`
Expected: PASS — all new tests plus the full existing rendering suite (the no-attachments path is byte-identical to before, so existing assertions hold).

- [ ] **Step 5: Commit**

```bash
git add packages/agent-core-channel/src/agent_core_channel/rendering.py packages/agent-core-channel/tests/test_rendering.py
git commit -m "feat(channel): surface attachment block in inbox render (#76)"
```

---

## Task 5: Retention sweep + lifecycle wiring + config params

**Files:**
- Modify: `packages/agent-core-discord/src/agent_core_discord/endpoint.py` (`__init__` ~232-272; `start()` ~565-568; start-rollback ~574-585; `stop()` ~831-837; add sweep methods near `_pending_acks_sweep_loop` ~1196)
- Test: `packages/agent-core-discord/tests/test_attachment_retention.py` (new)

A second background task mirroring `_pending_acks_sweep_loop`: deletes envelope dirs older than the retention window, then enforces an aggregate byte cap oldest-first.

- [ ] **Step 1: Write the failing tests**

Create `packages/agent-core-discord/tests/test_attachment_retention.py`:

```python
"""Retention sweep for auto-downloaded attachments (#76)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from agent_core_discord.endpoint import DiscordEndpoint


def _ep(tmp_path: Path, **kw) -> DiscordEndpoint:
    return DiscordEndpoint(
        name="discord-test",
        target="agent-x",
        token_env="X_TOKEN",
        attachments_dir=tmp_path,
        **kw,
    )


def _mkenv(root: Path, env_id: str, *, nbytes: int, age_seconds: float) -> Path:
    d = root / env_id
    d.mkdir(parents=True, exist_ok=True)
    f = d / "file.bin"
    f.write_bytes(b"x" * nbytes)
    old = time.time() - age_seconds
    os.utime(d, (old, old))
    os.utime(f, (old, old))
    return d


def test_sweep_evicts_dirs_older_than_retention(tmp_path):
    ep = _ep(tmp_path, attachment_retention_days=1)
    fresh = _mkenv(tmp_path, "fresh", nbytes=10, age_seconds=0)
    stale = _mkenv(tmp_path, "stale", nbytes=10, age_seconds=2 * 86400)
    ep._sweep_attachments_once()
    assert fresh.exists()
    assert not stale.exists()


def test_sweep_enforces_size_cap_oldest_first(tmp_path):
    ep = _ep(tmp_path, attachment_retention_days=3650, attachment_max_total_bytes=250)
    old = _mkenv(tmp_path, "old", nbytes=200, age_seconds=3000)
    mid = _mkenv(tmp_path, "mid", nbytes=200, age_seconds=2000)
    new = _mkenv(tmp_path, "new", nbytes=200, age_seconds=1000)
    ep._sweep_attachments_once()
    # 600 bytes total, cap 250 → evict oldest-first until <= cap.
    assert not old.exists()
    assert not mid.exists()
    assert new.exists()


def test_sweep_skips_unsafe_and_does_not_crash(tmp_path, monkeypatch):
    ep = _ep(tmp_path, attachment_retention_days=1)
    _mkenv(tmp_path, "stale", nbytes=10, age_seconds=2 * 86400)

    import shutil

    def boom(path):
        raise PermissionError("locked")

    monkeypatch.setattr(shutil, "rmtree", boom)
    # Must not raise even though every delete fails.
    ep._sweep_attachments_once()


def test_sweep_noop_when_dir_missing(tmp_path):
    ep = _ep(tmp_path / "does-not-exist", attachment_retention_days=1)
    ep._sweep_attachments_once()  # must not raise


@pytest.mark.asyncio
async def test_attachment_sweep_task_cancels_cleanly(tmp_path):
    import asyncio

    ep = _ep(tmp_path, attachment_sweep_seconds=0.01, attachment_retention_days=1)
    # Construct the loop task directly to assert lifecycle semantics without
    # standing up a real Discord client.
    ep._attachment_sweep_task = asyncio.create_task(ep._attachment_sweep_loop())
    await asyncio.sleep(0.03)
    ep._attachment_sweep_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ep._attachment_sweep_task
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/agent-core-discord/tests/test_attachment_retention.py::test_sweep_evicts_dirs_older_than_retention -v`
Expected: FAIL with `AttributeError: 'DiscordEndpoint' object has no attribute '_sweep_attachments_once'`.

- [ ] **Step 3a: Add `__init__` params**

In `packages/agent-core-discord/src/agent_core_discord/endpoint.py`, in `__init__`, alongside the existing `pending_acks_*` keyword parameters (around line 237-239), add three params:

```python
        attachment_retention_days: int = 30,
        attachment_max_total_bytes: int = 1_073_741_824,
        attachment_sweep_seconds: float = 3600.0,
```

And alongside `self.pending_acks_sweep_seconds = pending_acks_sweep_seconds` (around line 259), add:

```python
        self.attachment_retention_days = attachment_retention_days
        self.attachment_max_total_bytes = attachment_max_total_bytes
        self.attachment_sweep_seconds = attachment_sweep_seconds
```

And alongside `self._sweep_task: asyncio.Task | None = None` (line 271), add:

```python
        self._attachment_sweep_task: asyncio.Task | None = None
```

- [ ] **Step 3b: Add the sweep methods**

Add these two methods immediately after `_pending_acks_sweep_loop` (which ends at line 1206):

```python
    def _sweep_attachments_once(self) -> int:
        """One retention pass over <attachments_dir>.

        Age first: delete any <env_id>/ dir whose mtime is older than
        attachment_retention_days. Then size cap: while total bytes exceed
        attachment_max_total_bytes, delete whole dirs oldest-first by mtime.
        Whole-directory deletes only; never partial. A failed delete is
        logged and skipped — the sweep never raises into its loop.
        Returns the number of directories evicted.
        """
        import shutil

        root = self.attachments_dir
        try:
            if not root.exists():
                return 0
            root_resolved = root.resolve()
            entries = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            return 0

        def _safe_rmtree(d: Path) -> bool:
            try:
                if d.resolve().parent != root_resolved:
                    return False  # never walk outside the attachments root
                shutil.rmtree(d)
                return True
            except Exception:
                log.exception(
                    "discord(%s): attachment sweep failed to delete %s",
                    self.name,
                    d,
                )
                return False

        evicted = 0
        cutoff = time.time() - (self.attachment_retention_days * 86400)
        survivors: list[tuple[float, int, Path]] = []
        for d in entries:
            try:
                mtime = d.stat().st_mtime
                size = sum(
                    f.stat().st_size for f in d.rglob("*") if f.is_file()
                )
            except OSError:
                continue
            if mtime < cutoff:
                if _safe_rmtree(d):
                    evicted += 1
            else:
                survivors.append((mtime, size, d))

        total = sum(s for _, s, _ in survivors)
        if total > self.attachment_max_total_bytes:
            survivors.sort(key=lambda t: t[0])  # oldest first
            for mtime, size, d in survivors:
                if total <= self.attachment_max_total_bytes:
                    break
                if _safe_rmtree(d):
                    total -= size
                    evicted += 1
        return evicted

    async def _attachment_sweep_loop(self) -> None:
        """Periodic attachment retention sweep. Runs until cancelled by stop()."""
        try:
            while True:
                await asyncio.sleep(self.attachment_sweep_seconds)
                try:
                    self._sweep_attachments_once()
                except Exception:
                    log.exception(
                        "discord endpoint '%s': attachment sweep iteration failed",
                        self.name,
                    )
        except asyncio.CancelledError:
            raise
```

- [ ] **Step 3c: Wire the task into start / start-rollback / stop**

In `start()`, immediately after the existing `self._sweep_task = asyncio.create_task(self._pending_acks_sweep_loop(), name=...)` block (lines 565-568), add:

```python
            self._attachment_sweep_task = asyncio.create_task(
                self._attachment_sweep_loop(),
                name=f"discord-endpoint-{self.name}-attach-sweep",
            )
```

In the start-rollback `except BaseException:` block, immediately after the existing `self._sweep_task` cancel/await/`= None` sequence (lines 574-585), add the mirror:

```python
            if self._attachment_sweep_task is not None:
                self._attachment_sweep_task.cancel()
                try:
                    await self._attachment_sweep_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "discord endpoint '%s': attachment sweep raised during start rollback",
                        self.name,
                    )
                self._attachment_sweep_task = None
```

In `stop()`, immediately after the existing `self._sweep_task` cancel/await block (lines 831-837), add the mirror:

```python
        if self._attachment_sweep_task is not None:
            self._attachment_sweep_task.cancel()
            try:
                await self._attachment_sweep_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "discord endpoint '%s': attachment sweep raised during stop",
                    self.name,
                )
            self._attachment_sweep_task = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/agent-core-discord/tests/test_attachment_retention.py -v`
Then the full discord suite for regressions: `uv run pytest packages/agent-core-discord/ -v`
Expected: PASS for all.

- [ ] **Step 5: Verify config param passthrough**

The `builtin.discord` endpoint factory must forward yaml `params:` into `DiscordEndpoint(**params)`. Confirm:

Run: `grep -rn "DiscordEndpoint(" packages/core/src/agent_core/endpoints/ packages/agent-core-discord/src/`
Inspect the construction site. If it already does `DiscordEndpoint(**params)` or explicitly threads kwargs, the three new params work via defaults + optional yaml override with no further change — proceed. If it enumerates params explicitly (no `**`), add `attachment_retention_days`, `attachment_max_total_bytes`, `attachment_sweep_seconds` to that call, reading from the params dict with the same defaults. Make the minimal change that lets `agent_core.yaml` set them; do not refactor the factory.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-core-discord/src/agent_core_discord/endpoint.py packages/agent-core-discord/tests/test_attachment_retention.py
# plus the factory file if Step 5 required a change
git commit -m "feat(discord): daemon-side attachment retention sweep (#76)"
```

---

## Task 6: Document the new endpoint params

**Files:**
- Modify: the canonical discord-endpoint config example (identified in Step 1).

- [ ] **Step 1: Locate the canonical discord config example**

Run: `grep -rln "builtin.discord" docs/ agent_core.yaml 2>/dev/null`
Choose the target by this rule: if the root `agent_core.yaml` template contains a `builtin.discord` block, edit that (it is the canonical template); otherwise edit the `docs/` example file the grep returns. If the grep returns nothing, add a commented `builtin.discord` params block to the root `agent_core.yaml`. Exactly one file is edited.

- [ ] **Step 2: Add the documented params**

In the chosen file, in (or adjacent to) the `builtin.discord` endpoint's `params:`, add commented documentation:

```yaml
      # Attachment auto-download retention (#76). Optional — defaults shown.
      # Inbound attachments are downloaded to
      # ~/.agent-core/attachments/<endpoint>/<envelope_id>/ and a periodic
      # sweep enforces both limits (age first, then aggregate size cap,
      # oldest-first).
      attachment_retention_days: 30          # delete envelope dirs older than this
      attachment_max_total_bytes: 1073741824 # 1 GiB aggregate cap
      attachment_sweep_seconds: 3600         # sweep cadence
```

- [ ] **Step 3: Commit**

```bash
git add <the file you edited>
git commit -m "docs(discord): document attachment retention params (#76)"
```

---

## Task 7: Manual verification (runbook only — no commit)

Final acceptance before PR. Same shape as #79's manual step. Jeff runs this on his box.

- [ ] **Step 1:** Refresh the daemon onto this branch's code: from the repo root, `git checkout feat/issue-76-attachment-autodownload`, then `uv run agent-core daemon refresh`. Confirm `daemon status` shows the new sha. (Note per #91: restart Pepper's session afterward so her MCP reconnects.)
- [ ] **Step 2:** Send Pepper a Discord message with **one image** and text ("did you see this?").
- [ ] **Step 3:** Confirm Pepper's inbox render shows the text AND an `[attachment: <name> (<type>, <size>) → <path>]` line, and that she can `Read` the path directly — **one round-trip, no `list_pending`/`consume`/fetch**.
- [ ] **Step 4:** Multi-attachment regression: send **3 images in one message**. Confirm 3 distinct `[attachment: …]` lines with 3 readable paths.
- [ ] **Step 5:** Non-image regression: send a PDF (or any non-image file). Confirm it surfaces identically (path readable, no image-specific handling).
- [ ] **Step 6:** Retention spot-check: confirm `~/.agent-core/attachments/discord-pepper/<envelope_id>/` exists with the files; optionally set `attachment_retention_days: 0` in config, `daemon refresh`, and confirm the sweep clears old dirs without disturbing the live daemon.
- [ ] **Step 7:** Capture the inbox-render text for the single-image case and paste it into the PR as acceptance evidence.

---

## After all tasks: open PR

```bash
git push -u origin feat/issue-76-attachment-autodownload
gh pr create --repo jeffrichley/agent_core --base main --head feat/issue-76-attachment-autodownload \
  --title "feat(discord): auto-download attachments + surface local paths (#76)" \
  --body "Closes #76. <summary + the 7 acceptance criteria mapped to tests + the manual-verification render evidence from Task 7>"
```

(Do not push/open/merge without Jeff's explicit go-ahead — same gate as #79.)
