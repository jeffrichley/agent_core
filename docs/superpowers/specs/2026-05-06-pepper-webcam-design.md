# Pepper Webcam Endpoint — Design

**Status:** Draft (brainstorming approved 2026-05-06)
**Author:** Jeff + Claude Opus 4.7 (1M context)
**Related:** [Issue #39](https://github.com/jeffrichley/agent_core/issues/39) — generic MCP tool-call audit (filed concurrent with this design)

## Goal

Give Pepper an MCP tool surface that lets her capture a single frame from a connected webcam **anytime she wants**, see the image immediately, and have a saved PNG file she can later attach to Discord, archive, or revisit. The motivating use case is open-ended: she wants to start "experiencing" her physical environment, not serve one specific workflow.

## Non-goals

- **Continuous / ambient awareness.** Scheduled background captures are a v2 concern; v1 is on-demand only. We don't yet know what ambient should look like, so we don't build it.
- **Multi-platform parity testing.** v1 ships and is verified on Windows. macOS / Linux work in principle (OpenCV is cross-platform) but aren't tested until someone actually runs Pepper on them.
- **Cleanup tooling.** No `delete_capture` tool, no automatic retention policy. Pepper trusts the operator to clean up disk; we revisit if usage demands it.
- **A generic MCP tool-call audit log.** Webcam ships its own local audit log. The broader gap (every MCP tool invocation across all endpoints should be auditable) is tracked separately as [issue #39](https://github.com/jeffrichley/agent_core/issues/39).

## Architecture

### New package: `packages/agent-core-webcam/`

Peer to `agent-core-discord` and `agent-core-briefs`. Standard layout:

```
packages/agent-core-webcam/
├── pyproject.toml                # opencv-python dep lives here, NOT in core
├── src/agent_core_webcam/
│   ├── __init__.py
│   ├── plugin.py                 # pluggy hookimpls (mirror briefs/plugin.py)
│   ├── endpoint.py               # WebcamEndpoint (the bus endpoint)
│   ├── capture.py                # OpenCV-backed capture; isolated for fakability
│   ├── mcp.py                    # register_webcam_tools(mcp, endpoint, ...)
│   └── audit.py                  # JSONL audit log writer
└── tests/
    ├── conftest.py               # FakeCameraBackend fixture
    ├── test_endpoint.py          # Tier 1 unit tests
    ├── test_real_opencv.py       # Tier 2 integration (gated)
    ├── test_mcp_wiring.py        # Tier 3 plugin/wiring tests
    └── test_audit_log.py         # JSONL writer in isolation
```

Heavy `opencv-python` dependency lives in this package only. `agent_core` core stays unchanged.

### Pluggy plugin hooks

Mirrors `agent-core-briefs/plugin.py` exactly:

- `register_endpoint_types()` → `{"builtin.webcam": WebcamEndpoint}`
- `reserved_endpoint_params()` → `["webcam"]` (the param name on `claude_code_mcp` that names which webcam endpoint to mount)
- `wire_endpoints_after_registration()` → for each `ClaudeCodeMCPEndpoint` whose yaml has `params.webcam: <name>`, look up the webcam endpoint by name and append a deferred mounter to the MCP endpoint's `deferred_tool_mounters`. The mounter is invoked once during `bus.start()` and registers the two webcam tools on the FastMCP server via `register_webcam_tools(mcp, endpoint=<webcam>, bus_handle=...)`.

### Endpoint config (yaml)

```yaml
- type: builtin.claude_code_mcp
  name: pepper
  params:
    mount: /mcp/pepper
    briefs_orchestrator: briefs.pepper
    webcam: webcam-pepper                                      # ← new

- type: builtin.webcam
  name: webcam-pepper
  description: "Pepper's webcam endpoint."
  params:
    enabled: true                                              # hard kill switch
    captures_root: "~/.agent-core/webcam/pepper"               # where PNGs land
    audit_log_path: "~/.agent-core/webcam/pepper/audit.jsonl"  # who/when/what
    default_camera_index: 0
    default_resolution: [1280, 720]
    max_resolution: [3840, 2160]                               # caps oversized requests
    capture_timeout_seconds: 3.0                               # OpenCV read timeout
```

**Defaults if a param is omitted:** `enabled=true`, `captures_root=~/.agent-core/webcam/<name>`, `audit_log_path=<captures_root>/audit.jsonl`, `default_camera_index=0`, `default_resolution=[1280, 720]`, `max_resolution=[3840, 2160]`, `capture_timeout_seconds=3.0`.

### Per-agent (not shared)

Agents that want webcam access get their own `builtin.webcam` endpoint instance, mirroring the briefs pattern. Agents that don't (e.g., testbot) simply omit it from the yaml. Per-agent buys: own captures dir, own audit log, own kill switch, opt-in tool surface. Multi-agent setups stay clean.

### Bus envelope behavior

`WebcamEndpoint` implements the standard `Endpoint` protocol (`start`, `deliver`, `stop`) but `deliver` is essentially a no-op — webcam is a tool-only surface, no inbox, no agent-to-agent envelopes. The endpoint exists so MCP tools have somewhere to live and config to read.

## Tool surface

Two MCP tools mounted onto Pepper's `/mcp/pepper` surface.

### `capture_webcam_frame`

**Description (what Claude reads):** *Capture a single frame from a connected webcam. Returns the image inline (so you can see it immediately) plus a saved file path you can later attach to a Discord message, archive, or revisit. Use camera_index=0 for the default camera; call list_cameras to enumerate other devices.*

**Args (Pydantic-validated at MCP boundary):**
```python
{
  "camera_index": int = 0,                # which camera (0 = default)
  "resolution": [int, int] | None = None, # [width, height]; None = endpoint default
  "save": bool = True,                    # write PNG to disk; if False, in-memory only
  "note": str | None = None,              # optional audit-log annotation
}
```

**Returns** (MCP tool result — array of content blocks):
```python
[
    ImageContent(type="image", data=<base64 PNG bytes>, mimeType="image/png"),
    TextContent(type="text", text=(
        "Captured frame from camera 0 (Integrated Camera) at 1280x720.\n"
        "Path: C:\\Users\\jeffr\\.agent-core\\webcam\\pepper\\2026-05-06\\142307-481.png\n"
        "Timestamp: 2026-05-06T14:23:07.481-04:00\n"
        "Filesize: 184312 bytes"
    )),
]
```

The image content block is the part Pepper's vision model reasons about directly. The text block carries the path + metadata so she can pass it to other tools (e.g., `send_discord_message({files: [<path>]})`) without parsing.

### `list_cameras`

**Description:** *List all webcams detected on this host. Use the returned `index` as the `camera_index` argument to `capture_webcam_frame`.*

**Args:** none.

**Returns** (text content block — JSON):
```json
[
  {"index": 0, "name": "Integrated Camera", "available": true},
  {"index": 1, "name": "Logitech BRIO", "available": true}
]
```

Camera names come from OpenCV / DirectShow on Windows when available; on platforms where names aren't reliably exposed, falls back to `"Camera 0"`, `"Camera 1"`. The `available` flag confirms the device opened cleanly during enumeration (i.e., not currently in use by another app).

## Data flow

### Single-capture sequence (happy path)

```
Pepper                MCP host             WebcamEndpoint        OpenCV / OS
  │                     │                      │                    │
  │ tools/call          │                      │                    │
  │ capture_webcam_frame│                      │                    │
  ├────────────────────►│                      │                    │
  │                     │ capture(idx=0, ...)  │                    │
  │                     ├─────────────────────►│                    │
  │                     │                      │ VideoCapture(0)    │
  │                     │                      ├───────────────────►│
  │                     │                      │ <opens, LED on>    │
  │                     │                      │ read() → frame     │
  │                     │                      │◄───────────────────┤
  │                     │                      │ release()          │
  │                     │                      ├───────────────────►│
  │                     │                      │ <LED off>          │
  │                     │                      │                    │
  │                     │                      │ encode PNG         │
  │                     │                      │ write to disk      │
  │                     │                      │ append audit JSONL │
  │                     │  ImageContent + meta │                    │
  │                     │◄─────────────────────┤                    │
  │  image + text       │                      │                    │
  │◄────────────────────┤                      │                    │
```

**Total wall-clock:** ~200-500ms on a typical USB webcam (warm-up dominated). Camera is **opened and released per-call** — privacy-honest LED behavior, and we don't have to manage long-lived hardware state across the daemon's lifetime.

### Storage layout

```
~/.agent-core/webcam/<endpoint_name>/
├── 2026-05-06/
│   ├── 142307-481.png
│   ├── 143012-203.png
│   └── ...
├── 2026-05-07/
└── audit.jsonl
```

Date-bucketed folders make manual pruning trivial (delete a day, delete a month). Filenames are `HHMMSS-millis.png` so they sort lexically and don't collide on rapid captures.

### Color space

OpenCV returns BGR by default; the endpoint converts to RGB before PNG-encoding. PNGs are sRGB. No exotic color spaces.

### Downstream re-use

Three paths Pepper can take from a captured frame:

1. **Look at it** — zero further work; vision model handles the image content block in her next turn.
2. **Send it to Jeff** — she takes the path from the text block and calls `mcp__discord__send_discord_message({channel_id, files: ["<path>"]})`. The Discord adapter already accepts local paths (`packages/agent-core-discord/src/agent_core_discord/endpoint.py:1119-1137`).
3. **Archive it** — she copies the path into her Memory notes, gbrain, or wherever she keeps things she wants to remember. The endpoint doesn't prescribe; she decides.

## Configuration, privacy, audit

### Kill switch behavior

When `enabled: false`, the endpoint still loads and registers, but `capture_webcam_frame` and `list_cameras` return a structured error:

```
error: webcam endpoint is disabled (enabled=false in config). Ask the operator if this is unexpected.
```

She gets a clean, agent-readable explanation instead of a silently-missing tool. Flipping the switch requires a daemon restart (config-time decision, not runtime).

### Audit log (JSONL, one line per tool invocation)

`~/.agent-core/webcam/<endpoint>/audit.jsonl`:

```jsonl
{"timestamp": "2026-05-06T14:23:07.481-04:00", "tool": "capture_webcam_frame", "camera_index": 0, "camera_name": "Integrated Camera", "resolution": [1280, 720], "save": true, "file_path": "C:\\Users\\jeffr\\.agent-core\\webcam\\pepper\\2026-05-06\\142307-481.png", "filesize": 184312, "note": "checking what's on my desk", "session_id": "<mcp session id if available>", "result": "ok"}
{"timestamp": "2026-05-06T14:23:42.117-04:00", "tool": "list_cameras", "result": "ok", "camera_count": 2}
{"timestamp": "2026-05-06T14:24:01.220-04:00", "tool": "capture_webcam_frame", "camera_index": 0, "result": "error", "error": "camera 0 busy (in use by another process)"}
```

**Schema covers:** every successful capture (with file path, so audit + filesystem stay in sync), every `list_cameras` call (cheap, useful for noticing repeated probing), every error (so failures don't disappear). The `session_id` is the MCP session identifier if exposed by the host — best-effort; not a hard requirement.

**Rotation:** none in v1. Single file, append-only. If it grows unwieldy, add daily rotation later (mirror `daily_raw_jsonl`'s design). The audit log is meant to be readable by Jeff and by Pepper if she wants to introspect her own history.

### Retention

**v1: indefinite.** No auto-prune. Pepper trusts Jeff to clean up; Jeff trusts the disk to be big enough. We can add a sweep job later (e.g., "delete captures older than 30 days") once we see actual usage patterns. Premature retention policy would either annoy Pepper (deletes a frame she wanted) or be too lenient to matter.

### Privacy posture

- **Hardware LED is the user-visible signal** — camera opens, LED on; camera releases, LED off. We do not suppress it.
- **No pre-warming at endpoint startup** — pre-warming would mean turning the LED on at daemon start, which contradicts the LED-reflects-active-capture posture.
- **`enabled: false` is the operator's hard veto** — config-time decision, restart-gated.
- **Audit log is append-only** — Pepper can read it but not retroactively edit it.
- **Captures stay on local disk** — no upload, no cloud sync, no network egress unless Pepper deliberately attaches one to a Discord message.

## Error handling & edge cases

Each tool maps every failure to a short, agent-readable error message. No exceptions cross the MCP boundary; Pepper always sees structured text she can act on.

### `capture_webcam_frame` failure modes

| Failure | What Pepper sees | Audit log entry |
|---|---|---|
| `enabled: false` | `error: webcam endpoint is disabled (enabled=false in config). Ask the operator if this is unexpected.` | `result: "error", error: "endpoint disabled"` |
| Camera index doesn't exist | `error: no camera at index 3 (host has 2 cameras: indices 0, 1). Call list_cameras to see what's available.` | `result: "error", error: "camera 3 not found"` |
| Camera busy / in use by another app | `error: camera 0 (Integrated Camera) is busy. Likely in use by another application (Zoom, browser, etc.). Try again in a moment.` | `result: "error", error: "camera busy"` |
| OpenCV `read()` returned no frame | `error: camera opened but returned no frame within 3.0s. Camera may be initializing or obstructed.` | `result: "error", error: "read timeout"` |
| Requested resolution exceeds `max_resolution` | `error: requested resolution 7680x4320 exceeds configured max 3840x2160.` | `result: "error", error: "resolution capped"` |
| Disk write fails (permissions, disk full) | `error: capture succeeded but failed to write to disk: <OSError message>. Image is unavailable; retry or set save=false.` | `result: "error", error: "<OSError>", file_path: null` |
| OpenCV import fails (package not installed) | bus daemon fails fast at startup with a clear error message pointing at the missing `agent-core-webcam` package | n/a (endpoint never started) |
| Pydantic args validation fails (e.g., wrong shape for `resolution`) | clean validation error before anything touches OpenCV | not logged (rejected at MCP boundary) |

### `list_cameras` failure modes

| Failure | What Pepper sees |
|---|---|
| `enabled: false` | Same kill-switch message as above. |
| Enumeration partially fails (one device opens, others throw) | Returns the cameras that DID open; entries for failures include `available: false` with the reason. List is best-effort, never raises. |

### Concurrency

Two `capture_webcam_frame` calls in flight simultaneously to the **same** camera (Pepper makes parallel tool calls). The endpoint serializes camera access via an `asyncio.Lock` keyed on `camera_index` — the second call waits for the first to finish (max ~500ms). Result: both succeed in order, no partial-frame corruption. Captures against **different** cameras can proceed in parallel.

### OpenCV initialization is slow on first call

First-ever `VideoCapture(0)` on Windows can take 1-2s while DirectShow initializes the device tree. Subsequent calls (within the daemon's lifetime) are fast. We accept the first-call cost; we do **not** pre-warm at endpoint startup (privacy posture above).

### Camera disconnected mid-daemon

USB unplugged after the daemon started. Next `capture` call: `read()` returns no frame → "read timeout" error. Pepper retries; if still failing, calls `list_cameras` and notices the device is gone. No proactive detection — driven by failed calls.

## Testing strategy

Three tiers.

### Tier 1 — Unit tests with `FakeCameraBackend`

The endpoint accepts a `camera_backend` injection point (default: `OpenCVCameraBackend`). Tests substitute `FakeCameraBackend` which produces deterministic frames without touching OpenCV. The fake **strictly mirrors the real backend's behavior** — including refusing operations OpenCV refuses (per project memory: fakes for third-party libs must refuse argument shapes the real lib would refuse).

```python
class FakeCameraBackend:
    def list_cameras(self) -> list[CameraInfo]: ...
    def capture(self, index: int, resolution: tuple[int, int]) -> bytes: ...
    # Test modes:
    #   .with_cameras([0, 1])       — N cameras available
    #   .with_busy(0)               — index 0 raises CameraBusyError
    #   .with_missing(3)            — index 3 raises CameraNotFoundError
    #   .with_read_timeout(0)       — index 0 returns no frame within timeout
```

**Tests:**
- `test_capture_returns_image_content_block` — happy path, returns ImageContent + TextContent
- `test_capture_writes_png_to_disk` — file lands at expected date-bucketed path
- `test_capture_appends_audit_log_entry` — audit JSONL has the right keys
- `test_capture_save_false_skips_disk_write` — only inline image, no file
- `test_capture_disabled_kill_switch` — endpoint returns kill-switch error
- `test_capture_camera_not_found` — clean error message + audit entry
- `test_capture_camera_busy` — clean error message + audit entry
- `test_capture_read_timeout` — clean error message + audit entry
- `test_capture_resolution_exceeds_max` — capped + error
- `test_capture_disk_write_failure` — OSError → user-readable error + audit
- `test_capture_concurrent_calls_serialize` — two calls to same camera complete in order via lock
- `test_capture_concurrent_calls_different_cameras_parallel` — different indices don't block
- `test_capture_converts_bgr_to_rgb` — output PNG is RGB
- `test_list_cameras_returns_enumeration` — happy path
- `test_list_cameras_partial_failure_returns_available_subset`
- `test_audit_log_records_list_cameras_calls`
- `test_pydantic_rejects_malformed_resolution` — args validation at MCP boundary

### Tier 2 — Real OpenCV integration test (gated)

One smoke test that imports and calls real OpenCV, verifying the `OpenCVCameraBackend` adapter actually compiles and the failure-mode mapping matches reality. Skipped by default (`pytest.mark.requires_camera`); runs only when `WEBCAM_INTEGRATION_TEST=1` is set. Not in CI — Jeff or developer runs it locally before merging.

```python
@pytest.mark.requires_camera
def test_real_opencv_open_release_no_frame_when_disconnected():
    # Validates that when no camera exists, OpenCVCameraBackend raises
    # the same exception type FakeCameraBackend raises in the same scenario.
```

### Tier 3 — Plugin discovery & MCP wiring tests

Mirrors `agent-core-briefs/tests/test_mcp_wiring.py`:

- `test_plugin_registers_endpoint_type` — `pluggy` discovery picks up `builtin.webcam`
- `test_plugin_reserves_webcam_param` — runner pops `webcam:` before constructing `claude_code_mcp`
- `test_wire_endpoints_pairs_mcp_with_webcam` — yaml with `webcam: webcam-pepper` results in webcam tools mounted on Pepper's MCP at start
- `test_wire_endpoints_raises_on_unknown_webcam_name` — clean ValueError on misconfig
- `test_wire_endpoints_raises_on_wrong_endpoint_type` — `webcam: discord-pepper` → ValueError

### Explicitly not tested in v1

- The actual quality / correctness of captured pixel data. We trust OpenCV.
- Webcam hardware behavior across platforms (macOS AVFoundation, Linux V4L2). v1 ships and is verified on Windows; cross-platform testing happens when someone actually runs Pepper on Mac/Linux.
- MCP transport / streamable-HTTP behavior. Already covered by core's existing tests.

## Migration / rollout

1. Build `agent-core-webcam` package alongside existing peers; install as part of the agent-core monorepo.
2. Add the two yaml entries to Pepper's `~/.agent-core/agent_core.yaml` (claude_code_mcp gets the new `webcam:` param; new `builtin.webcam` endpoint added below).
3. Restart the bus daemon. Verify via direct `streamablehttp_client` probe of `/mcp/pepper` that `capture_webcam_frame` and `list_cameras` are present.
4. `/exit` + relaunch Pepper's session so her MCP cache picks up the new tools (per existing issue #37 — until generic `tools/list_changed` lands, restart is the workaround).
5. Smoke test: ask Pepper to look at her environment. Verify image returns + PNG lands on disk + audit log gets an entry.

## Open future work (not v1)

- **[Issue #39](https://github.com/jeffrichley/agent_core/issues/39)** — generic MCP tool-call audit logging in the HTTP MCP host. When it lands, webcam's local audit may become redundant or stay as a domain-specific finer-grained record.
- **Ambient mode** — scheduled background captures into a rolling buffer Pepper can browse. Defer until on-demand usage patterns inform the design.
- **Sweep / retention job** — daily/weekly cleanup of old captures past N days. Add when disk pressure is real.
- **Daily rotation of the audit log** — mirror `daily_raw_jsonl` if the single-file log gets unwieldy.
- **macOS / Linux verification** — exercise the real-OpenCV test on those platforms when Pepper actually runs there.

## Decisions log

| Decision | Choice | Rationale |
|---|---|---|
| Package location | New `packages/agent-core-webcam/` | Heavy `opencv-python` dep stays out of `core`; matches Discord/briefs precedent |
| Tool count | 2 (`capture_webcam_frame`, `list_cameras`) | Small surface, one-per-action MCP pattern, YAGNI on cleanup tools |
| Per-agent vs shared | Per-agent endpoint instances | Matches briefs pattern; per-agent kill switch + audit log + captures dir |
| Camera lifecycle | Open + release per call | Privacy-honest LED behavior; no long-lived hardware state |
| Inline image return | Yes, ImageContent + TextContent | Pepper sees frame the same turn; path enables downstream uses |
| Disk write default | Always (with `save=False` opt-out) | Can't predict if *this* capture will get sent/archived later |
| Retention | Indefinite v1 | Premature policy is worse than none; revisit on actual usage |
| Kill switch | `enabled: false`, restart-gated | Config-time decision, consistent with rest of agent-core |
| Audit log scope | Webcam-local v1 + file generic gap (#39) | High privacy concern doesn't wait on cross-cutting fix |
| First-call slowness | Accept it | Pre-warming would turn LED on at daemon start (privacy violation) |
| Concurrency | `asyncio.Lock` per `camera_index` | Avoid OpenCV partial-frame issues; allow parallel across different cameras |
