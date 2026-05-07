# Issue #44 — Handoff Worker Tail + Loud Failure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the handoff worker from silently writing 268-byte stub handoffs marked `state: "ready"` when the bundled Claude SDK fails on oversized transcripts. Read only the tail of the transcript; let summarization failures propagate to the existing `_write_failed` path; capture subprocess stderr to status, daemon log, and per-job log file.

**Architecture:** All changes are in `packages/core/src/agent_core/endpoints/handoff_jobs.py` and its test file. New helpers (`_read_transcript_tail`, `_summarize_stderr`, `_capture_subprocess_stderr`); modified methods (`_call_agent_sdk` wraps stderr capture, `_extract_handoff` loses its silent fallback); two new constructor params for tail bounds plus one for the per-job log dir. The consumer side (`HandoffInjector`) already handles `state: "failed"` correctly — no changes needed there.

**Tech Stack:** Python 3.12+, pytest with `pytest.mark.asyncio`, `httpx`, `claude_agent_sdk`. Existing test patterns in `packages/core/tests/test_handoff_jobs_endpoint.py` use config-driven bus setup with `tmp_path` and `monkeypatch` fixtures.

**Spec:** `docs/superpowers/specs/2026-05-07-issue-44-handoff-worker-tail-fail-loud-design.md`

**Branch:** `fix/issue-44-handoff-worker-tail-fail-loud` (already created)

---

## File Structure

**Modified:**
- `packages/core/src/agent_core/endpoints/handoff_jobs.py`
  - Add module-level helper: `_read_transcript_tail`
  - Add static helpers on `HandoffJobsEndpoint`: `_summarize_stderr`, `_capture_subprocess_stderr`
  - Add constructor params: `transcript_tail_max_bytes`, `transcript_tail_max_messages`, `jobs_log_dir`
  - Modify `_process_job` call site to use the tail helper
  - Split SDK invocation: `_call_agent_sdk` (outer wrapper, stderr capture on failure) + new `_run_sdk_query` (inner, the actual `claude_agent_sdk.query()` call). Failure tests monkeypatch the inner so the outer wrapper runs.
  - Simplify `_extract_handoff` (remove silent stub)
  - Remove obsolete `_read_transcript_text`

**Modified:**
- `packages/core/tests/test_handoff_jobs_endpoint.py`
  - Add unit tests for the new helpers
  - Add integration tests for tail behavior, failure handling, and stderr capture

No other files touched. No schema changes. No new dependencies.

---

## Task 1: Tail-read helper (pure function)

**Why first:** Pure function, no async, no bus state. Establishes the tail semantics in isolation before wiring.

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/handoff_jobs.py` (add module-level function near the top, after the imports and before the `HandoffJobRequest` class)
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (add tests at the end of the file)

- [ ] **Step 1.1: Write the failing tests**

Add to `packages/core/tests/test_handoff_jobs_endpoint.py`:

```python
import json as _json  # alias to avoid shadowing the existing top-level `json`

from agent_core.endpoints.handoff_jobs import _read_transcript_tail


def test_read_transcript_tail_returns_whole_file_when_small(tmp_path):
    f = tmp_path / "small.jsonl"
    content = '{"a":1}\n{"b":2}\n'
    f.write_text(content, encoding="utf-8")

    result = _read_transcript_tail(f, max_bytes=1024, max_messages=100)
    assert result == content


def test_read_transcript_tail_caps_by_bytes_aligned_to_newline(tmp_path):
    f = tmp_path / "big.jsonl"
    lines = [f'{{"i":{i:06d},"data":"{"x" * 80}"}}' for i in range(100)]
    content = "\n".join(lines) + "\n"
    f.write_text(content, encoding="utf-8")
    assert len(content.encode("utf-8")) > 1024

    result = _read_transcript_tail(f, max_bytes=1024, max_messages=10000)

    assert len(result.encode("utf-8")) <= 1024
    first_line = result.split("\n", 1)[0]
    parsed = _json.loads(first_line)
    assert "i" in parsed
    assert result.endswith("\n")


def test_read_transcript_tail_caps_by_message_count(tmp_path):
    f = tmp_path / "many.jsonl"
    lines = [f'{{"i":{i}}}' for i in range(50)]
    content = "\n".join(lines) + "\n"
    f.write_text(content, encoding="utf-8")

    result = _read_transcript_tail(f, max_bytes=10**9, max_messages=10)
    result_lines = [ln for ln in result.split("\n") if ln]
    assert len(result_lines) == 10
    assert _json.loads(result_lines[0]) == {"i": 40}
    assert _json.loads(result_lines[-1]) == {"i": 49}


def test_read_transcript_tail_drops_partial_first_line_after_byte_seek(tmp_path):
    f = tmp_path / "big.jsonl"
    lines = [f'{{"line":{i},"x":"{"a" * 100}"}}' for i in range(20)]
    content = "\n".join(lines) + "\n"
    f.write_text(content, encoding="utf-8")

    result = _read_transcript_tail(f, max_bytes=250, max_messages=10000)

    for line in result.split("\n"):
        if line:
            _json.loads(line)  # raises ValueError on partial JSON


def test_read_transcript_tail_handles_empty_file(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    result = _read_transcript_tail(f, max_bytes=1024, max_messages=100)
    assert result == ""


def test_read_transcript_tail_handles_file_with_no_trailing_newline(tmp_path):
    f = tmp_path / "no_trail.jsonl"
    content = '{"a":1}\n{"b":2}'
    f.write_text(content, encoding="utf-8")
    result = _read_transcript_tail(f, max_bytes=1024, max_messages=100)
    assert '{"a":1}' in result
    assert '{"b":2}' in result
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "read_transcript_tail" -v`

Expected: FAIL with `ImportError` on `_read_transcript_tail` (function doesn't exist yet).

- [ ] **Step 1.3: Implement the helper**

Add to `packages/core/src/agent_core/endpoints/handoff_jobs.py` after the `_default_transcript_root` function and before the `HandoffJobRequest` class:

```python
def _read_transcript_tail(
    transcript_path: Path,
    *,
    max_bytes: int = 256 * 1024,
    max_messages: int = 200,
) -> str:
    """Return the tail of a jsonl transcript, line-aligned and capped both ways.

    For files at or below ``max_bytes`` returns the full file. For larger
    files seeks ``max_bytes`` from the end, drops the first (potentially
    partial) line, and additionally caps to the last ``max_messages`` lines
    if the byte tail still exceeds that count.
    """
    if not transcript_path.exists():
        raise FileNotFoundError(f"transcript does not exist: {transcript_path}")

    file_size = transcript_path.stat().st_size
    if file_size == 0:
        return ""

    if file_size <= max_bytes:
        text = transcript_path.read_text(encoding="utf-8")
    else:
        with transcript_path.open("rb") as fh:
            fh.seek(file_size - max_bytes)
            tail_bytes = fh.read()
        # Drop the partial first line (always — even if the seek happened to
        # land on a newline, the next byte starts a new line cleanly).
        nl_index = tail_bytes.find(b"\n")
        if nl_index >= 0:
            tail_bytes = tail_bytes[nl_index + 1:]
        text = tail_bytes.decode("utf-8", errors="replace")

    # Cap by message count (line count) if needed.
    lines = text.split("\n")
    # ``split`` produces a trailing empty string when text ends with newline;
    # treat empty trailing entry as a sentinel rather than a message.
    has_trailing_newline = text.endswith("\n")
    if has_trailing_newline:
        lines = lines[:-1]

    if len(lines) > max_messages:
        lines = lines[-max_messages:]

    result = "\n".join(lines)
    if has_trailing_newline and result:
        result += "\n"
    return result
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "read_transcript_tail" -v`

Expected: 6 passed.

- [ ] **Step 1.5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "feat(handoff): add bounded tail-read helper for transcripts

Adds _read_transcript_tail with both byte and message bounds. File-size <=
max_bytes returns whole file; larger files seek from end, drop the first
partial line for jsonl validity, then cap by message count.

Refs #44"
```

---

## Task 2: Wire tail helper into the endpoint

**Why next:** Now that the helper is proven, swap the call site and add constructor params. Existing endpoint integration tests must still pass.

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/handoff_jobs.py`
  - `__init__` adds two new params
  - `_process_job` swaps `_read_transcript_text` for `_read_transcript_tail`
  - Remove the now-unused `_read_transcript_text` static method
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (one new integration test)

- [ ] **Step 2.1: Write the failing test**

Add this test to `packages/core/tests/test_handoff_jobs_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_handoff_worker_uses_tail_for_oversized_transcripts(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-tail.jsonl"

    # Build a transcript larger than the tail bound so we can see truncation.
    lines = [f'{{"i":{i},"data":"{"x" * 200}"}}\n' for i in range(2000)]
    transcript_path.write_text("".join(lines), encoding="utf-8")

    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
      transcript_tail_max_bytes: 4096
      transcript_tail_max_messages: 50
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    received_lengths: list[int] = []
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _capture_extract(self, req, transcript_text, *args, **kwargs):
                received_lengths.append(len(transcript_text.encode("utf-8")))
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _capture_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-tail",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            assert received_lengths, "_extract_handoff was not called"
            assert received_lengths[0] <= 4096
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py::test_handoff_worker_uses_tail_for_oversized_transcripts -v`

Expected: FAIL — config rejects unknown params or worker still reads whole file (received_lengths[0] > 4096).

- [ ] **Step 2.3: Add constructor params**

Edit `HandoffJobsEndpoint.__init__` in `packages/core/src/agent_core/endpoints/handoff_jobs.py`:

```python
def __init__(
    self,
    *,
    name: str,
    mount: str = "/internal/handoff-jobs",
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.25,
    transcript_tail_max_bytes: int = 256 * 1024,
    transcript_tail_max_messages: int = 200,
    jobs_log_dir: Path | None = None,
):
    self.name = name
    self.mount = mount
    self._max_attempts = max(1, max_attempts)
    self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
    self._transcript_tail_max_bytes = max(1, transcript_tail_max_bytes)
    self._transcript_tail_max_messages = max(1, transcript_tail_max_messages)
    self._jobs_log_dir = (
        jobs_log_dir
        if jobs_log_dir is not None
        else Path("~/.agent-core/handoffs/jobs").expanduser()
    )
    self._handle: BusHandle | None = None
    self._jobs: asyncio.Queue[_QueuedJob] = asyncio.Queue()
    self._worker_task: asyncio.Task[None] | None = None
    self._idempotency_index: dict[str, str] = {}
```

- [ ] **Step 2.4: Swap the call site in `_process_job`**

In `_process_job`, replace this block (currently around lines 191-194):

```python
        for attempt in range(1, self._max_attempts + 1):
            try:
                transcript_text = self._read_transcript_text(transcript_path)
                handoff_content = await self._extract_handoff(req, transcript_text)
```

With:

```python
        for attempt in range(1, self._max_attempts + 1):
            try:
                transcript_text = _read_transcript_tail(
                    transcript_path,
                    max_bytes=self._transcript_tail_max_bytes,
                    max_messages=self._transcript_tail_max_messages,
                )
                handoff_content = await self._extract_handoff(req, transcript_text)
```

- [ ] **Step 2.5: Remove the obsolete `_read_transcript_text` method**

Delete this static method (currently around lines 237-241):

```python
    @staticmethod
    def _read_transcript_text(transcript_path: Path) -> str:
        if not transcript_path.exists():
            raise FileNotFoundError(f"transcript does not exist: {transcript_path}")
        return transcript_path.read_text(encoding="utf-8")
```

- [ ] **Step 2.6: Run the new test and the full handoff-jobs test file**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -v`

Expected: all tests pass, including `test_handoff_worker_uses_tail_for_oversized_transcripts`. Existing tests should be unaffected — they use small transcripts that fit under the default 256 KB.

- [ ] **Step 2.7: Commit**

```bash
git add packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "feat(handoff): wire tail-read into worker, add constructor params

Adds transcript_tail_max_bytes / transcript_tail_max_messages / jobs_log_dir
constructor params to HandoffJobsEndpoint. _process_job now reads only the
bounded tail of the transcript instead of the whole file. Removes the
obsolete _read_transcript_text helper.

Refs #44"
```

---

## Task 3: Stderr summary helpers (pure functions)

**Why next:** Need both helpers in place before wiring stderr capture into `_call_agent_sdk`. Pure functions, easy to TDD.

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/handoff_jobs.py` (add two static methods on `HandoffJobsEndpoint`)
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (add unit tests)

- [ ] **Step 3.1: Write the failing tests**

Add to `packages/core/tests/test_handoff_jobs_endpoint.py`:

```python
def test_summarize_stderr_returns_full_text_when_short():
    text = "first line\nlast line"
    result = HandoffJobsEndpoint._summarize_stderr(text, max_chars=500)
    assert "first line" in result
    assert "last line" in result


def test_summarize_stderr_truncates_with_ellipsis_when_long():
    long_middle = "middle\n" * 1000
    text = f"first line\n{long_middle}last line"
    result = HandoffJobsEndpoint._summarize_stderr(text, max_chars=80)
    assert len(result) <= 80
    assert "first line" in result
    assert "last line" in result
    assert "..." in result


def test_summarize_stderr_skips_empty_lines():
    text = "\n\nfirst real line\n\n\nlast real line\n\n"
    result = HandoffJobsEndpoint._summarize_stderr(text, max_chars=500)
    assert "first real line" in result
    assert "last real line" in result


def test_summarize_stderr_handles_empty_input():
    result = HandoffJobsEndpoint._summarize_stderr("", max_chars=500)
    assert result == ""


def test_capture_subprocess_stderr_extracts_from_chained_exception():
    inner = RuntimeError("subprocess died: file too large")
    outer = RuntimeError("Command failed with exit code 1")
    outer.__cause__ = inner
    result = HandoffJobsEndpoint._capture_subprocess_stderr(outer)
    assert "file too large" in result or "subprocess died" in result


def test_capture_subprocess_stderr_falls_back_to_repr_when_no_chain():
    exc = ValueError("nothing structured here")
    result = HandoffJobsEndpoint._capture_subprocess_stderr(exc)
    assert result
    assert "nothing structured here" in result
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "summarize_stderr or capture_subprocess_stderr" -v`

Expected: FAIL with `AttributeError` — methods don't exist.

- [ ] **Step 3.3: Implement the helpers**

Add to `HandoffJobsEndpoint` class in `packages/core/src/agent_core/endpoints/handoff_jobs.py` (place near the other static helpers, e.g. before `_resolve_under_root`):

```python
    @staticmethod
    def _summarize_stderr(text: str, *, max_chars: int = 500) -> str:
        """Return a short summary of stderr text capped at ``max_chars``.

        Uses the first and last non-empty lines, joined with " ... " when the
        full text would exceed the cap. Returns the full text untruncated when
        it already fits.
        """
        if not text:
            return ""
        non_empty = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if not non_empty:
            return text[:max_chars]
        if len(text) <= max_chars:
            return text
        first = non_empty[0]
        last = non_empty[-1] if len(non_empty) > 1 else ""
        if not last or first == last:
            return first[:max_chars]
        sep = " ... "
        # Reserve room for separator and last line; truncate first if needed.
        budget = max_chars - len(sep) - len(last)
        if budget <= 0:
            # last line alone is too long; fall back to truncated first line
            return first[:max_chars]
        return f"{first[:budget]}{sep}{last}"

    @staticmethod
    def _capture_subprocess_stderr(exc: BaseException) -> str:
        """Best-effort extraction of subprocess stderr from an exception.

        Walks the ``__cause__`` chain looking for messages that mention stderr
        or subprocess exit. Falls back to ``repr(exc)`` when nothing structured
        is found.
        """
        parts: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            msg = str(current)
            if msg:
                parts.append(msg)
            current = current.__cause__ or current.__context__

        if parts:
            return "\n".join(parts)
        return repr(exc)
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "summarize_stderr or capture_subprocess_stderr" -v`

Expected: 6 passed.

- [ ] **Step 3.5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "feat(handoff): add stderr summary + capture helpers

_summarize_stderr returns a bounded first-line + last-line summary for
the status file's error field. _capture_subprocess_stderr walks the
exception cause chain looking for subprocess output, falling back to
repr(exc) when nothing structured is available.

Refs #44"
```

---

## Task 4: Wire stderr capture into `_call_agent_sdk`

**Why next:** Helpers are ready. This task adds the per-job log file write + daemon log line on SDK failure, and threads `job_id` through the call signatures. No behavior change to status yet — that comes in Task 5.

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/handoff_jobs.py`
  - `_extract_handoff` signature gains `job_id`
  - `_call_agent_sdk` signature gains `job_id` and wraps body in try/except
  - `_process_job` passes `job.job_id` through
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (add test for per-job log)

- [ ] **Step 4.1: Write the failing test**

Add to `packages/core/tests/test_handoff_jobs_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_handoff_worker_writes_per_job_log_on_sdk_failure(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-fail.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"
    jobs_log_dir = tmp_path / "jobs_logs"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
      jobs_log_dir: {jobs_log_dir}
      retry_backoff_seconds: 0.0
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _failing_inner(self, *, req, transcript_text):
                inner = RuntimeError("subprocess stderr: transcript too large")
                outer = RuntimeError("Command failed with exit code 1")
                outer.__cause__ = inner
                raise outer

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _failing_inner)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-fail",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            log_file = jobs_log_dir / f"{job_id}.log"
            assert log_file.exists(), f"per-job log not written at {log_file}"
            log_content = log_file.read_text(encoding="utf-8")
            assert "transcript too large" in log_content or "exit code 1" in log_content
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py::test_handoff_worker_writes_per_job_log_on_sdk_failure -v`

Expected: FAIL — `_call_agent_sdk` doesn't accept `job_id` kwarg yet, OR no log file written.

- [ ] **Step 4.3: Thread `job_id` and split SDK invocation**

Edit `_extract_handoff` in `packages/core/src/agent_core/endpoints/handoff_jobs.py`. Replace the existing body (currently lines 243-257) with:

```python
    async def _extract_handoff(
        self, req: HandoffJobRequest, transcript_text: str, job_id: str
    ) -> str:
        try:
            response_text = await self._call_agent_sdk(
                req=req, transcript_text=transcript_text, job_id=job_id,
            )
            if response_text.strip():
                return response_text
            raise RuntimeError("empty handoff extraction")
        except Exception as exc:
            generated_at = datetime.now(UTC).isoformat()
            return (
                f"# Handoff ({req.agent_name})\n\n"
                f"- session_id: {req.session_id}\n"
                f"- event: {req.event}\n"
                f"- generated_at: {generated_at}\n\n"
                f"Extraction fallback used: {exc}"
            )
```

> Note: The silent fallback in `_extract_handoff` is *kept* in this task. Task 5 removes it. Splitting like this lets us test stderr capture independently from the behavior change.

Now split the existing `_call_agent_sdk` (currently lines 259-318) into two methods. The outer wrapper handles stderr capture; the inner runs the actual SDK call. **Tests can monkeypatch the inner so the outer wrapper still runs.** Replace the entire `_call_agent_sdk` method with these two methods:

```python
    async def _call_agent_sdk(
        self, *, req: HandoffJobRequest, transcript_text: str, job_id: str
    ) -> str:
        """Run the SDK summarizer; capture stderr to per-job log on failure."""
        try:
            return await self._run_sdk_query(req=req, transcript_text=transcript_text)
        except Exception as exc:
            stderr_text = self._capture_subprocess_stderr(exc)
            job_log_path = self._jobs_log_dir / f"{job_id}.log"
            try:
                job_log_path.parent.mkdir(parents=True, exist_ok=True)
                job_log_path.write_text(stderr_text, encoding="utf-8")
            except OSError:
                log.exception(
                    "failed to write per-job log for handoff job %s at %s",
                    job_id, job_log_path,
                )
            log.error(
                "handoff job %s SDK call failed; full stderr at %s: %s",
                job_id, job_log_path, str(exc)[:500],
            )
            summary = self._summarize_stderr(stderr_text, max_chars=500)
            raise RuntimeError(f"summarizer failed: {summary}") from exc

    async def _run_sdk_query(
        self, *, req: HandoffJobRequest, transcript_text: str
    ) -> str:
        """Inner SDK invocation. Tests monkeypatch this; the outer wrapper still runs."""
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        prompt = f"""You are writing a handoff note for {req.agent_name} for continuity between sessions.
Write from {req.agent_name}'s perspective. Based on the transcript below, produce:

## What We Were Working On
- specific bullets

## Decisions Made
- specific bullets

## Emotional Temperature
- one sentence

## Open Threads
- specific bullets

## Observations
- specific bullets

Rules:
- Keep each section to 2-5 bullets where applicable.
- Skip empty sections.
- Be concrete: mention files, tools, errors, and decisions.
- If truly trivial transcript, respond with exactly HANDOFF_EMPTY.

Session metadata:
- session_id: {req.session_id}
- event: {req.event}

Transcript:
{transcript_text}
"""
        response_text = ""
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[],
                max_turns=2,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

        if response_text.strip() == "HANDOFF_EMPTY":
            return (
                f"# Handoff ({req.agent_name})\n\n"
                f"- session_id: {req.session_id}\n"
                f"- event: {req.event}\n\n"
                "No significant content to hand off."
            )
        return response_text
```

- [ ] **Step 4.4: Update `_process_job` call site**

In `_process_job`, change the call to `_extract_handoff` (around line 194) from:

```python
                handoff_content = await self._extract_handoff(req, transcript_text)
```

To:

```python
                handoff_content = await self._extract_handoff(
                    req, transcript_text, job.job_id,
                )
```

- [ ] **Step 4.5: Run the targeted test and the full file**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py::test_handoff_worker_writes_per_job_log_on_sdk_failure -v`

Expected: PASS — log file exists with stderr content.

Then: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -v`

Expected: all tests pass. Existing tests still pass because the `_extract_handoff` fallback stub is still in place (it's removed in Task 5).

- [ ] **Step 4.6: Commit**

```bash
git add packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "feat(handoff): capture SDK subprocess stderr to per-job log on failure

When _call_agent_sdk raises, capture whatever stderr we can extract from
the exception chain to ~/.agent-core/handoffs/jobs/<job_id>.log, log a
daemon line pointing at it, and re-raise with a short summary. Threads
job_id through _extract_handoff and _call_agent_sdk signatures.

The silent fallback in _extract_handoff is still in place and is removed
in the next commit. This split lets us test stderr capture independent
of the behavior change.

Refs #44"
```

---

## Task 5: Remove the silent stub fallback (the load-bearing behavior change)

**Why next:** Stderr capture is in place; per-job log is being written. Now make summarization failures actually fail loudly: status `state: "failed"`, `handoff.md` untouched.

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/handoff_jobs.py` (remove try/except in `_extract_handoff`)
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (three new tests for the loud-failure behavior)

- [ ] **Step 5.1: Write the failing tests**

Add to `packages/core/tests/test_handoff_jobs_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_handoff_worker_marks_failed_when_sdk_raises(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-marked.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
      retry_backoff_seconds: 0.0
      jobs_log_dir: {tmp_path / "jobs_logs"}
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _always_fail(self, *, req, transcript_text):
                raise RuntimeError("subprocess stderr: kaboom")

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _always_fail)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-marked",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "failed"
            assert status.get("error")
            assert "kaboom" in status["error"] or "summarizer failed" in status["error"]
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_worker_does_not_overwrite_handoff_md_when_failed(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-preserve.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    pre_existing = "# Last good handoff\n\n- session: prior\n- decision: keep this content\n"
    handoff_path.write_text(pre_existing, encoding="utf-8")

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
      retry_backoff_seconds: 0.0
      jobs_log_dir: {tmp_path / "jobs_logs"}
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _always_fail(self, *, req, transcript_text):
                raise RuntimeError("subprocess stderr: kaboom")

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _always_fail)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-preserve",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            assert handoff_path.read_text(encoding="utf-8") == pre_existing
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_handoff_worker_truncates_stderr_in_status_error_field(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-trunc.jsonl"
    transcript_path.write_text('{"message":{"role":"user","content":"hi"}}\n', encoding="utf-8")
    handoff_path = vault_root / "handoff.md"
    status_path = vault_root / "handoff-status.json"

    cfg = tmp_path / "agent_core.yaml"
    cfg.write_text(
        f"""
bus:
  storage_path: {tmp_path / "bus.sqlite"}
http:
  bind_host: 127.0.0.1
  bind_port: 0
endpoints:
  - type: builtin.handoff_jobs
    name: handoff-jobs
    params:
      mount: /internal/handoff-jobs
      retry_backoff_seconds: 0.0
      jobs_log_dir: {tmp_path / "jobs_logs"}
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            huge_msg = "first line of error\n" + ("noise " * 5000) + "\nlast line of error"

            async def _huge_fail(self, *, req, transcript_text):
                raise RuntimeError(huge_msg)

            monkeypatch.setattr(type(endpoint), "_run_sdk_query", _huge_fail)

            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-trunc",
                "event": "SessionEnd",
                "agent_name": "pepper",
                "vault_root": str(vault_root),
                "handoff_path": str(handoff_path),
                "handoff_status_path": str(status_path),
                "transcript_path": str(transcript_path),
                "transcript_root": str(transcript_root),
                "requested_at": datetime.now(UTC).isoformat(),
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
            assert resp.status_code == 202

            for _ in range(60):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state in ("ready", "failed"):
                        break
                await asyncio.sleep(0.05)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            assert status["state"] == "failed"
            err = status["error"]
            # Status error must be bounded — allow generous slack for outer wrapping.
            assert len(err) <= 700
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "marks_failed or does_not_overwrite or truncates_stderr" -v`

Expected: FAIL — silent fallback still in place writes a stub and marks status `ready`, so the assertions on `state == "failed"` fail.

- [ ] **Step 5.3: Remove the silent stub fallback**

Edit `_extract_handoff` in `packages/core/src/agent_core/endpoints/handoff_jobs.py`. Replace the body (just modified in Task 4) with the simplified version:

```python
    async def _extract_handoff(
        self, req: HandoffJobRequest, transcript_text: str, job_id: str
    ) -> str:
        response_text = await self._call_agent_sdk(
            req=req, transcript_text=transcript_text, job_id=job_id,
        )
        if response_text.strip():
            return response_text
        raise RuntimeError("empty handoff extraction")
```

The try/except + fallback stub is gone. Exceptions from `_call_agent_sdk` (which already capture stderr to the per-job log and wrap in a `RuntimeError("summarizer failed: ...")`) propagate to `_process_job`'s `for attempt in range(...)` loop, which already retries up to `_max_attempts` then calls `_write_failed`.

- [ ] **Step 5.4: Run all targeted tests + full file**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -v`

Expected: all tests pass. The three new failure-behavior tests pass; existing success-path tests still pass (their mocked `_extract_handoff` returns content directly, so the removed fallback is never exercised).

- [ ] **Step 5.5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "fix(handoff): remove silent stub fallback; surface failures loudly

When summarization fails, exceptions now propagate to _process_job's
existing retry + _write_failed path. Status reflects state=failed with
the actual error; handoff.md is left untouched (HandoffInjector already
treats this as 'last-known-good from earlier successful cycle').

Closes #44"
```

---

## Task 6: Full suite + manual verification

**Why last:** Final regression sweep across the whole core package, then a smoke test against a real Pepper transcript to validate the change end-to-end.

**Files:**
- None modified.

- [ ] **Step 6.1: Run the full core test suite**

Run: `uv run pytest packages/core/tests/ -v`

Expected: all tests pass. Investigate any unrelated failures before declaring victory — they may indicate accidental side effects.

- [ ] **Step 6.2: Run the linter on the modified file**

Run: `uv run ruff check packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py`

Expected: no violations. Fix any that surface (likely import order or line length).

- [ ] **Step 6.3: Manual verification — small transcript golden path**

In a Python REPL or a quick scratch script, instantiate `HandoffJobsEndpoint` with default tail bounds, point it at a small handcrafted transcript jsonl, and confirm:

- The worker reads the whole file (file size < 256 KB).
- A successful mock SDK response produces a real handoff.md.
- Status file shows `state: "ready"` with a `content_sha256`.

This validates that small-transcript behavior is unchanged from before the fix.

- [ ] **Step 6.4: Manual verification — Pepper's real transcript**

Locate Pepper's current session jsonl (likely under `~/.claude/projects/C--Users-jeffr--pepper/<uuid>.jsonl`). Note the file size — if > 256 KB, this validates the tail path.

Construct a job request payload that points at it (do NOT use Pepper's real vault paths — write to a scratch dir to avoid clobbering her live handoff.md). Submit to a locally-running daemon or directly invoke the worker with a real config.

Confirm:

- Worker completes (does not OOM, does not hang).
- If SDK succeeds: `handoff.md` is real and based on the tail of the transcript.
- If SDK fails: status is `failed`, error is informative (not "Check stderr output for details"), `~/.agent-core/handoffs/jobs/<job_id>.log` exists with subprocess stderr.

If the SDK fails on the tail too, that's the legitimate "transcript still too large" case and the fix is correct — agent reads the `failed` injector branch and uses MEMORY.md / dailies.

- [ ] **Step 6.5: Push and open PR**

```bash
git push -u origin fix/issue-44-handoff-worker-tail-fail-loud
gh pr create --base main --title "fix(handoff): bounded tail-read + loud failure (#44)" --body "$(cat <<'EOF'
## Summary

Stops the handoff worker from silently writing stub handoffs marked
\`state: "ready"\` when the bundled Claude SDK fails on oversized
transcripts. Worker now reads only the bounded tail
(\`min(256 KB, 200 messages)\`); summarization failures propagate to
the existing \`_write_failed\` path; subprocess stderr is captured to
a per-job log file.

The \`HandoffInjector\` consumer already handles \`state: "failed"\`
correctly — labels existing \`handoff.md\` as last-known-good from an
earlier successful cycle and points the agent at MEMORY.md / dailies.

Closes #44.

## Spec + Plan

- Spec: \`docs/superpowers/specs/2026-05-07-issue-44-handoff-worker-tail-fail-loud-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-07-issue-44-handoff-worker-tail-fail-loud-plan.md\`

## Test plan

- [x] New unit tests for \`_read_transcript_tail\`, \`_summarize_stderr\`, \`_capture_subprocess_stderr\`
- [x] New integration tests for tail bound, loud-failure status, no-overwrite, truncated error, per-job log
- [x] Existing \`HandoffJobsEndpoint\` tests still pass
- [x] Manual verification on Pepper's real transcript

## After merge

Unblocks #45 (SessionEnd-on-\`--continue\` investigation). A clean
\`/exit\` test will tell us whether the originally suspected "Bug 1"
was a real Claude Code behavior gap or a measurement artifact.
EOF
)"
```

- [ ] **Step 6.6: Update the open-issues roadmap**

After the PR merges, mark Phase 1 as partially complete in `docs/superpowers/plans/2026-05-07-open-issues-cleanup-roadmap.md`'s status table. Note that #44 is closed; #42 / #43 / #45 remain.

---

## Self-review checklist (run after writing this plan)

**Spec coverage:** Each spec section has a corresponding task.
- Tail-read helper → Task 1
- Worker simplification → Task 4 (signature change) + Task 5 (silent stub removal)
- Stderr capture → Task 3 (helpers) + Task 4 (wiring)
- Three stderr destinations → Task 4 (per-job + daemon log) + Task 5 (status field via existing `_write_failed` path)
- Constructor params → Task 2 (`tail_max_bytes`, `tail_max_messages`) + Task 4 (`jobs_log_dir`)
- All 12 tests from spec → covered across Tasks 1, 2, 4, 5

**Placeholder scan:** No TBDs, no "implement appropriately," no "similar to Task N." Code blocks are concrete.

**Type consistency:** `_extract_handoff(req, transcript_text, job_id)` signature is consistent across Task 4 and Task 5. `_call_agent_sdk` keyword-only signature `(*, req, transcript_text, job_id)` is consistent. `_run_sdk_query` keyword-only signature `(*, req, transcript_text)` (no `job_id` — that's handled by the outer wrapper) is consistent across Task 4 introduction and Task 5 failure tests' monkeypatches. `_jobs_log_dir` attribute name is consistent. `_summarize_stderr` and `_capture_subprocess_stderr` static methods used identically across tasks.

**Granularity:** Each step is 2-5 minutes (write test, run test, write code, run test, commit). Six tasks total. Each task ends in a commit.
