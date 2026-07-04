# Spec: fix access-config reload loop dies silently on schema-invalid JSON (issue #218)

## Goal

`DiscordEndpoint._access_config_reload_loop` can be permanently killed by a schema-valid-JSON-but-invalid-`AccessConfig` file (e.g. `{"channels": 5}`). The JSON pre-validation passes, but the subsequent `load_access_config(path)` call raises `TypeError`/`ValueError` when building the dataclass; the narrow `except asyncio.CancelledError: raise`-only outer handler then lets the exception escape the `while True` loop, terminating the task with no log and no recovery. See [issue #218](https://github.com/jeffrichley/agent_core/issues/218).

## Acceptance criteria

- A schema-valid-JSON-but-invalid-`AccessConfig` file (e.g. `{"channels": 5}`) written while the loop is running → the loop logs `WARNING`, keeps `self._access` unchanged, does **not** update `self._access_config_mtime` (so it retries next cycle), and the task remains alive. A new test covers all three behaviours.
- The existing 7 tests in `test_access_reload.py` still pass unchanged.
- There is **no second filesystem read** between validation and commit: the `AccessConfig` is built from the already-read `raw_text`, not via a second `load_access_config(path)` call.
- `new_failures_count == 0`; `just check` passes green.

## Approach

**Pattern naming.** No GoF pattern fits. This is the **Defensive Loop** idiom: a background polling task that consumes external input (a user-editable file) must never terminate on malformed input — it logs and retries. The engineering principle is SRP: the reload task's responsibility is "poll, keep config current, survive"; validating that a file will never contain bad data is not its responsibility.

**Root cause.** The current loop (lines 1517–1558, `endpoint.py`) has a two-step read: it calls `read_text` + `json.loads` to pre-validate JSON, then calls `load_access_config(self.access_config_path)` — which reads the file a second time and builds the dataclass. The inner except clause is `except (OSError, json.JSONDecodeError)`. If the second read races with a partial write, or if the JSON is valid but the fields have wrong types (e.g. `channels` is `5`), `load_access_config` raises `TypeError`/`ValueError`. That exception escapes the `while True:` because the only outer handler is `except asyncio.CancelledError: raise`. The task is dead from that point on.

**Fix — two-part change, one file each.**

*Part 1 — `access.py`:* Extract a private helper `_build_access_config(raw: dict[str, Any], source: str) -> AccessConfig` that contains the dict-to-dataclass conversion currently inlined in `load_access_config` (lines 61–88). `load_access_config` becomes a thin wrapper that reads + JSON-parses the file and delegates to `_build_access_config`. The helper's contract: raises `TypeError` or `ValueError` on bad-typed field values; callers decide how to handle those. No behaviour change to `load_access_config`'s external contract — the existing tests in `test_access.py` pass without modification.

*Part 2 — `endpoint.py`:* Rewrite `_access_config_reload_loop`'s inner try/except block to:
1. Read once (`raw_text = read_text(...)`) — eliminate the double-read.
2. Call `_build_access_config(json.loads(raw_text), str(self.access_config_path))` — single parse, no second I/O.
3. Catch `except Exception` instead of `except (OSError, json.JSONDecodeError)` — any error (JSONDecodeError, TypeError, ValueError, OSError) hits the same WARN + continue path. `asyncio.CancelledError` is a `BaseException` subclass in Python 3.8+, so `except Exception` does NOT catch it; the outer `except asyncio.CancelledError: raise` still fires for clean shutdown.

`self._access` and `self._access_config_mtime` are only updated **after** the try block succeeds — the `continue` in the except clause prevents reaching those assignments, so a bad file never poisons the mtime cache (retrying is correct behaviour).

The `_attachment_sweep_loop` (lines 1560–1573) already uses `except Exception: log.exception(...)` as the right pattern for keeping a background loop alive; the reload loop is brought to the same standard.

## Sub-requests (topologically sorted)

1. **Extract `_build_access_config` in `packages/agent-core-discord/src/agent_core_discord/access.py`.**  
   After line 23 (`_VALID_DM_POLICIES = ...`), add the new private helper. Then collapse `load_access_config` to call it. See "File-level changes" for the exact signatures.

2. **Update `_access_config_reload_loop` in `packages/agent-core-discord/src/agent_core_discord/endpoint.py`.**  
   Replace lines 1537–1556 (the pre-validate + `load_access_config` call through the closing `)` of the current `log.info` block) with a single-read + `_build_access_config` call inside `except Exception`. Also add `_build_access_config` to the `from agent_core_discord.access import ...` line (line 38).

3. **Add new tests to `packages/agent-core-discord/tests/test_access_reload.py`.**  
   Two new tests mirroring the existing malformed-JSON split: one that asserts config unchanged + task alive, one that asserts a warning was emitted. See "File-level changes" for exact test code.

## File-level changes

| File | Change |
|---|---|
| `packages/agent-core-discord/src/agent_core_discord/access.py` | **Modify.** Add `_build_access_config(raw, source)` private helper (extracts lines 61–88 of current `load_access_config`). Refactor `load_access_config` to delegate to it. |
| `packages/agent-core-discord/src/agent_core_discord/endpoint.py` | **Modify.** Add `_build_access_config` to the `access` import. Replace the inner try/except block in `_access_config_reload_loop` (lines 1537–1556) with single-read + `_build_access_config` + `except Exception`. |
| `packages/agent-core-discord/tests/test_access_reload.py` | **Modify.** Add `test_access_reload_keeps_config_on_schema_invalid_json` and `test_access_reload_warns_on_schema_invalid_json`. |

### Exact code the Worker should write

**`access.py` — new `_build_access_config` helper (insert after line 24, before the blank line before `@dataclass`):**

```python
def _build_access_config(raw: dict[str, Any], source: str = "<unknown>") -> AccessConfig:
    """Build an AccessConfig from a pre-parsed JSON dict.

    Raises TypeError or ValueError if field values have unexpected types
    (e.g. ``channels`` is an int instead of a dict).  Callers are
    responsible for catching these and deciding how to recover.
    """
    dm_policy = raw.get("dmPolicy", "open")
    if dm_policy not in _VALID_DM_POLICIES:
        log.warning(
            "access config %s: unknown dmPolicy %r; falling back to 'deny'",
            source,
            dm_policy,
        )
        dm_policy = "deny"
    raw_allowed_bot_ids = raw.get("allowedBotIds", [])
    if not isinstance(raw_allowed_bot_ids, list):
        log.warning(
            "access config %s: allowedBotIds must be a list; got %s — falling back to empty",
            source,
            type(raw_allowed_bot_ids).__name__,
        )
        raw_allowed_bot_ids = []
    return AccessConfig(
        dm_policy=dm_policy,  # type: ignore[arg-type]
        allow_from=list(raw.get("allowFrom", [])),
        channels=dict(raw.get("channels", {})),
        ack_reaction=raw.get("ackReaction", "👀"),
        allowed_bot_ids=[str(b) for b in raw_allowed_bot_ids],
    )
```

**`access.py` — updated `load_access_config` body (replace lines 56–88):**

```python
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.exception("failed to parse access config at %s; using defaults", p)
        return AccessConfig()
    return _build_access_config(raw, str(p))
```

**`endpoint.py` — updated import line 38:**

```python
from agent_core_discord.access import AccessConfig, InboundContext, _build_access_config, gate_message, load_access_config
```

**`endpoint.py` — updated inner try/except in `_access_config_reload_loop` (replace lines 1537–1556):**

```python
                # File changed — parse once; any error keeps previous config and retries.
                try:
                    raw_text = self.access_config_path.read_text(encoding="utf-8")
                    new_access = _build_access_config(
                        json.loads(raw_text), str(self.access_config_path)
                    )
                except Exception as exc:
                    log.warning(
                        "discord(%s): access config reload skipped (read/parse/schema error), "
                        "keeping previous config: %s",
                        self.name,
                        exc,
                    )
                    continue
                self._access = new_access
                self._access_config_mtime = current_mtime
                log.info(
                    "discord(%s): access config reloaded (channels=%d, dmPolicy=%s)",
                    self.name,
                    len(new_access.channels),
                    new_access.dm_policy,
                )
```

**`test_access_reload.py` — two new tests (append at end of file):**

```python
@pytest.mark.asyncio
async def test_access_reload_keeps_config_on_schema_invalid_json(monkeypatch, tmp_path):
    """Valid JSON but invalid AccessConfig schema keeps previous config and task alive."""
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        original_channels = dict(ep._access.channels)
        # "channels" is an int: valid JSON, invalid for AccessConfig (dict(...) raises TypeError)
        p.write_text(json.dumps({"channels": 5}), encoding="utf-8")
        await asyncio.sleep(0.05 + 0.1)
        # Config unchanged
        assert ep._access.channels == original_channels
        # Task still alive — the loop was NOT killed
        assert ep._access_reload_task is not None
        assert not ep._access_reload_task.done()
    finally:
        await ep.stop()


@pytest.mark.asyncio
async def test_access_reload_warns_on_schema_invalid_json(monkeypatch, tmp_path, caplog):
    """Valid JSON but invalid AccessConfig schema emits a WARNING log."""
    initial = {"channels": {"100": {}}}
    ep, p = await _start(monkeypatch, tmp_path, path_json=initial)
    try:
        p.write_text(json.dumps({"channels": 5}), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            await asyncio.sleep(0.05 + 0.1)
        assert any("access config reload" in rec.message for rec in caplog.records)
    finally:
        await ep.stop()
```

## Alternatives considered

- **Widen only the outer handler (leave double-read in place):** Change `except asyncio.CancelledError: raise` to `except Exception: log.warning(...); continue` without refactoring the inner try/except. This would keep the loop alive, but the TOCTOU double-read window remains. Rejected: the issue explicitly asks for parse-once, and it's no harder to do it right.
- **Add schema validation to `load_access_config` (catch TypeError/ValueError and return `AccessConfig()`):** Would make `load_access_config` silently swallow schema errors, which is worse — callers who call it at startup would silently fall back to permissive defaults on a bad file. The right boundary is to let the reload loop decide whether to silently recover (keep previous) versus fall through to defaults. Rejected.
- **Do nothing, document the crash:** The loop dies silently — no log, no label change, no user-visible signal. Hot-reload appears to work but doesn't. Rejected: the issue is unambiguous that this is a production resilience bug.

## Open questions

None. The fix scope, exact files, and behaviour change are fully specified by the issue and confirmed by reading the source.

## Out of scope

- Changing how `load_access_config` behaves at startup on a schema-invalid file (it currently falls back to permissive defaults on JSONDecodeError; leaving startup behaviour unchanged).
- Adding schema validation tests to `test_access.py` beyond what already exists — the issue doesn't request that.
- The reviewer-quality improvement tracked in foreman#417.
