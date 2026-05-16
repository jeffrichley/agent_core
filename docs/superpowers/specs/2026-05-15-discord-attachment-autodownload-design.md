# Discord attachment auto-download + local-path surfacing — design

**Issue:** [#76](https://github.com/jeffrichley/agent_core/issues/76) — `discord-pepper: auto-download attachments + surface local paths in push-based wake (extends #70)`

**Date:** 2026-05-15
**Status:** Approved, awaiting implementation

## Goal

When an inbound Discord message carries attachments, the agent should see — in a single round-trip — both the text and a readable local path to each attachment, with no on-demand fetch and no dependence on expiring Discord CDN URLs.

## Background

Live observation 2026-05-09: an image-with-text Discord message reached Pepper as text only. The attachment lived in `metadata["attachments"]` (CDN URL), which the push-based wake render did not surface, so Pepper had to call `list_pending`, then `curl` the signed CDN URL, then `Read` the copy — 3 calls + a network fetch for what should be one inline view. Worse, Discord CDN URLs are signed and time-bounded (`?ex=&is=&hm=`), so deferred fetches fail outright.

Two gaps: (1) the wake render ignores `metadata["attachments"]`; (2) even surfaced, the URL expires. The fix is to download at the adapter the moment the message arrives (URL still valid), persist locally, and surface the local path.

## Key existing infrastructure (reused, not rebuilt)

Exploration confirmed the download machinery already exists in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`:

- `_download_url` (~1453) — httpx GET, 30s timeout, returns `(bytes, content_type)`. Docstring explicitly: "Override in tests to avoid network."
- `_download_attachments` (~1469) — the agent-callable MCP tool that already persists URLs into `attachments_dir/<message_id>/` with safe filenames, a `relative_to()` path-traversal guard, and collision dedup (`stem-1.ext`).
- `_safe_filename` (~103) and `attachments_dir` default `~/.agent-core/attachments/<endpoint_name>` (~98-100).
- The inbound path (~907-936) currently builds `metadata["attachments"]` dicts with a literal `# no auto-download` comment (line 908).

`#76` is therefore mostly *wiring existing pieces together* + a retention sweep, not new download infrastructure.

## Non-goals

- Rendering attachment bytes into the agent's context window (harness concern).
- Voice-memo transcription (separate ticket).
- Cross-platform attachment normalization — v1 targets the Discord adapter; the `DiscordEndpoint` class is shared, so `discord-testbot` inherits the behavior automatically, which is acceptable.
- Per-file truncation for huge single files — v1 downloads everything; the aggregate size cap is the backstop. Truncation is a documented follow-up.
- Mirroring inbound attachments into `payload.attachments` (the `FileAttachment` model) — that is the outbound/send path; inbound has always used `metadata["attachments"]` and the asymmetry is preserved deliberately.

## Architecture

Entirely daemon-side, two components:

1. **Discord adapter** (`packages/agent-core-discord`) — auto-download at inbound, enrich metadata, retention sweep.
2. **Channel renderer** (`packages/agent-core-channel`) — surface the attachment block in the `<inbox>` body.

No changes to the bus core, the MCP endpoint, the wake notification, or the agent side.

### Inbound data flow (NEW steps in **bold**)

1. Discord message with attachments arrives at `DiscordEndpoint._on_message` (endpoint.py ~907).
2. Adapter builds the `metadata["attachments"]` dicts exactly as today (`filename`, `url`, `content_type`, `size_bytes`).
3. `Envelope` is constructed (id minted at ~939).
4. **Each attachment is downloaded synchronously** into `~/.agent-core/attachments/<endpoint_name>/<envelope_id>/<safe_filename>`, reusing the shared persist helper. Single attempt per attachment.
5. **Each metadata dict is enriched**: `local_path` (absolute str on success, `null` on failure) and `download_error` (short reason str, present only on failure). `filename`/`url`/`content_type`/`size_bytes` unchanged.
6. Envelope published as today. Bus, queueing, wake — unchanged.
7. Agent calls `list_pending()`; **`render_envelope()` appends an attachment block** to the TextMessage body from `metadata["attachments"]`.

Subdir keyed on **`envelope_id`** (not Discord `message_id`) so on-disk grouping matches the `<inbox envelope_id=...>` the agent sees. The existing `download_attachments` MCP tool keeps using `message_id`; both coexist under `attachments_dir`.

**Invariant:** the text message is never blocked or lost by attachment handling. Downloads are best-effort; failures degrade to url-only-plus-marker; the existing CDN-url metadata is preserved untouched.

## Component detail

### 1. Download integration (`agent-core-discord/endpoint.py`)

Extract the persist core shared by the existing MCP tool and the new inbound path:

```
async def _persist_attachment(self, *, url: str, filename: str, subdir: str) -> Path
```

Uses `_download_url`, `_safe_filename`, the `relative_to()` traversal guard, and the existing collision dedup. Returns the resolved path. The existing `download_attachments` MCP tool is refactored to call this helper (subdir = `message_id`) — no behavior change to that tool. The inbound path calls it with subdir = `envelope_id`.

Inbound integration point: after `env = Envelope(... id=...)` is constructed and before `publish()`, iterate attachments, call `_persist_attachment`, and enrich each metadata dict.

**Metadata shape** — each entry in `metadata["attachments"]`:

| field | today | after #76 |
|---|---|---|
| `filename` | yes | unchanged |
| `url` | yes (CDN, signed) | unchanged — preserved for debugging (acceptance #4) |
| `content_type` | yes | unchanged |
| `size_bytes` | yes | unchanged |
| `local_path` | — | NEW: absolute str on success, `null` on failure |
| `download_error` | — | NEW: present only on failure, short reason str |

**Ordering safety:** the existing `try/except BaseException` around `publish()` (~948), which rolls back inbound-mapping / awaiting-reply state, is widened to also wrap the download loop, so a mid-download crash cannot leave half-set state.

### 2. Wake-render block (`agent-core-channel/rendering.py`)

`render_envelope()` (~129-155) builds `<inbox {attrs}>\n{body}\n</inbox>`; the TextMessage body comes from `_render_text_message_body()` (~44-46). The attachment block appends to that body.

Source of truth: `envelope.metadata.get("attachments")` — render only when present and non-empty. Kind-agnostic (any envelope carrying `metadata["attachments"]` gets the block).

Rendered shape — one bracketed line per attachment, after the text body, separated by a blank line, in order:

Success:
```
[attachment: IMG_5468.png (image/jpeg, 835 KB) → C:\Users\jeffr\.agent-core\attachments\discord-pepper\<env_id>\IMG_5468.png]
```

Failure (url-only fallback):
```
[attachment: clip.mov (video/quicktime, 22 MB) — download failed (timeout); CDN url may be expired: https://cdn.discordapp.com/...]
```

Formatting decisions:
- Size humanized from `size_bytes`: powers of 1024, two significant figures, suffix `B`/`KB`/`MB`/`GB` (e.g. `512 B`, `835 KB`, `22 MB`, `1.4 GB`). Fall back to `<n> B` if `size_bytes` is missing or zero.
- Local path shown verbatim (absolute, OS-native) for zero-transformation `Read`.
- Plain text inside the existing `<inbox>` body — not a new XML tag (keeps the diff to the body renderer; the issue leaves tag-vs-inline to the implementer).
- Filenames/paths/urls pass through the same HTML-escape as the existing text body.
- Malformed `metadata["attachments"]` (not a list, missing keys): renderer skips the bad entry and never raises — a render exception would break the entire inbox delivery for that envelope, and attachment metadata is the least-trusted input.

### 3. Retention sweep (`agent-core-discord/endpoint.py`)

A second periodic background task in `DiscordEndpoint`, same shape as the existing pending-acks sweep (created in `start()`, cancelled in `stop()`).

Two limits, enforced every sweep (age first, then size on what remains):

1. **Age:** delete any `<attachments_dir>/<envelope_id>/` whose mtime is older than `attachment_retention_days` (default 30).
2. **Aggregate size cap:** if total bytes under `<attachments_dir>` exceed `attachment_max_total_bytes` (default 1 GB / `1073741824`), delete whole envelope dirs oldest-first by mtime until under the cap.

Deletion is whole-directory (`shutil.rmtree`) per envelope — never partial.

Config (endpoint params in `agent_core.yaml`, all optional with the defaults above):
```yaml
- type: builtin.discord
  name: discord-pepper
  params:
    attachment_retention_days: 30
    attachment_max_total_bytes: 1073741824
    attachment_sweep_seconds: 3600
```

Safety:
- Sweep only touches paths under `attachments_dir` (resolved + `relative_to` guard before any `rmtree`).
- A failed delete (locked/permission) is logged and skipped, never crashes the task; next sweep retries.
- Cadence hourly by default (retention is day-coarse; keeps the daemon quiet).
- `stop()` cancels the task with the same `CancelledError` handling as the pending-acks sweep.
- Sweep vs. live-write race: keyed on whole envelope dir; a just-arrived dir has fresh mtime (age-sweep skips it) and is newest (size-sweep evicts it last). No lock needed.

## Error handling & edge cases

- **Per-attachment download failure:** single attempt; any exception → `local_path: null` + `download_error`, other attachments unaffected, envelope still published. Never raises into the inbound path.
- **Zero attachments:** unchanged from today — no `metadata["attachments"]`, no download, no render block.
- **Duplicate filenames in one message:** existing dedup (`a.png`, `a-1.png`) — inherited.
- **Unsafe filename** (`../`, NUL, separators): existing `_safe_filename` + `relative_to()` guard — a rejected name becomes a `download_error`, not a crash.
- **Malformed `metadata["attachments"]` at render:** renderer skips bad entries, never raises.
- **Inbound crash mid-download:** widened `try/except BaseException` rolls back inbound state.
- **`attachments_dir` missing on first run:** `mkdir(parents=True, exist_ok=True)` before first write (existing helper).
- **Non-image files** (PDF, .mov, .txt): identical handling (acceptance #7); nothing image-specific anywhere.

## Testing

All daemon-side, no network (`_download_url` is the documented override seam).

**Discord adapter (`packages/agent-core-discord/tests/`):**
- `FakeAttachment` gains `content_type` + `size` (test-fakes-mirror-real discipline — both exist on real `discord.Attachment`).
- `test_endpoint_inbound.py`: 1 attachment → `metadata["attachments"][0]` has `local_path` to a real file with correct bytes; `url`/`filename`/`content_type`/`size_bytes` preserved.
- Multi-attachment (3) → 3 distinct `local_path`s, correct bytes each (acceptance #5).
- Download failure (stub `_download_url` raising) → `local_path: null`, `download_error` set, envelope still published, text intact.
- Crash mid-download → inbound-mapping/awaiting-reply state rolled back.
- Retention sweep: age eviction; size-cap oldest-first eviction; path-safety guard; locked-file skip-not-crash; `stop()` cancels the task.

**Channel renderer (`packages/agent-core-channel/tests/`):**
- Success entry → `[attachment: ... → <path>]`, humanized size.
- Failure entry → `download failed (...)` + url line.
- Multi-attachment ordering.
- Malformed `metadata["attachments"]` → degrades, text body still renders, no raise.
- No-attachments → body byte-identical to today (regression guard).

## Acceptance criteria (from issue #76)

1. Inbound TextMessage with ≥1 attachment → wake render includes per-attachment filename, content-type, size, local path. → renderer tests + live handoff.
2. Local path resolves to a real readable file, no network fetch. → adapter inbound test (bytes check).
3. Local file == original CDN bytes. → adapter test (byte/checksum compare).
4. Original CDN `url` preserved alongside new `local_path`. → adapter test (metadata shape).
5. Multi-attachment → distinct local paths, independently readable. → adapter + renderer multi tests.
6. Retention: old attachments cleaned, no silent disk growth. → sweep unit tests.
7. Non-text payloads handled uniformly. → adapter test with a PDF/.mov fake.

Live verification (manual handoff, same shape as #79): Pepper receives a Discord image, reads text+image in one round-trip (no `list_pending`/`consume`/fetch); 3-image multi-attachment regression; retention spot-check.

## Out of scope

- Per-file truncation for huge single files (aggregate cap is the v1 backstop).
- Voice-memo transcription.
- Cross-platform attachment normalization.
- Rendering attachment bytes into the agent context window.
- Mirroring inbound attachments into `payload.attachments`.

## Provenance

Surfaced 2026-05-09 from a live Pepper interaction; filed as #76. Design brainstormed 2026-05-15 with Jeff. Two forks resolved during brainstorm: retention owner = daemon-side sweep in `DiscordEndpoint` (the issue's vault-lint proposal rejected — vault-lint is a per-agent stub skill, files live in daemon-owned `~/.agent-core/`); download-failure behavior = single attempt, per-attachment url-only fallback with an error marker (message never lost).
