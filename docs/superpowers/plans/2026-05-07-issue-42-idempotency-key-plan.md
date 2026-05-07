# Issue #42 — Handoff Idempotency Key Per-Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `transcript_size` to the daemon's idempotency-key derivation so each real `/exit` of a `--continue` session produces a fresh key (and therefore a fresh handoff). Stop the hook from sending its own idempotency key — the daemon becomes the single source of truth.

**Architecture:** Two surgical edits across two files. The daemon's `_derive_idempotency_key` adds `transcript_size` (best-effort `os.stat()`, `-1` sentinel on `OSError`); the static method becomes an instance method. The hook's `handoff_writer.py` payload-building code stops setting `idempotency_key` so the daemon always derives. Six new tests across two test files lock in the behavior.

**Tech Stack:** Python 3.12+, pytest, `pytest.mark.asyncio` for integration tests, `httpx` for the daemon HTTP path, `unittest.mock.patch` for hook tests.

**Spec:** `docs/superpowers/specs/2026-05-07-issue-42-idempotency-key-design.md`

**Branch:** `fix/issue-42-idempotency-key` (already created)

---

## File Structure

**Modified:**
- `packages/core/src/agent_core/endpoints/handoff_jobs.py`
  - Convert `_derive_idempotency_key` from `@staticmethod` to instance method
  - Add `transcript_size` to the formula
  - Catch `OSError` and use `-1` sentinel
- `packages/core/src/agent_core/hooks/tools/handoff_writer.py`
  - Delete the `"idempotency_key": ...` line from the payload dict
- `packages/core/tests/test_handoff_jobs_endpoint.py`
  - 3 new unit tests for `_derive_idempotency_key`
  - 2 new integration tests for end-to-end dedup behavior
- `packages/core/tests/test_handoff_writer.py`
  - 1 new test asserting the hook does not include `idempotency_key` in its POST

No schema changes. No yaml changes. No new dependencies.

---

## Task 1: Daemon-side `_derive_idempotency_key` update

**Why first:** Updating the derivation in isolation lets us prove the new key shape works correctly via unit tests before touching the hook. Each test directly calls `_derive_idempotency_key` and asserts on the returned hash.

**Files:**
- Modify: `packages/core/src/agent_core/endpoints/handoff_jobs.py:506-509` (the existing `_derive_idempotency_key` static method)
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (add 3 unit tests at the end of the file)

- [ ] **Step 1.1: Write the failing tests**

Add to `packages/core/tests/test_handoff_jobs_endpoint.py` (alongside the existing test functions):

```python
def test_derive_idempotency_key_includes_transcript_size(tmp_path):
    transcript_a = tmp_path / "a.jsonl"
    transcript_a.write_text("a" * 100, encoding="utf-8")
    transcript_b = tmp_path / "b.jsonl"
    transcript_b.write_text("a" * 200, encoding="utf-8")

    endpoint = HandoffJobsEndpoint(name="test")

    req_a = HandoffJobRequest(
        session_id="session-x",
        event="SessionEnd",
        agent_name="pepper",
        vault_root=str(tmp_path),
        handoff_path=str(tmp_path / "handoff.md"),
        handoff_status_path=str(tmp_path / "handoff-status.json"),
        transcript_path=str(transcript_a),
        transcript_root=str(tmp_path),
        requested_at=datetime.now(UTC),
    )
    req_b = req_a.model_copy(update={"transcript_path": str(transcript_b)})

    key_a = endpoint._derive_idempotency_key(req_a)
    key_b = endpoint._derive_idempotency_key(req_b)

    assert key_a != key_b


def test_derive_idempotency_key_stable_for_same_size(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("hello world", encoding="utf-8")

    endpoint = HandoffJobsEndpoint(name="test")

    req = HandoffJobRequest(
        session_id="session-x",
        event="SessionEnd",
        agent_name="pepper",
        vault_root=str(tmp_path),
        handoff_path=str(tmp_path / "handoff.md"),
        handoff_status_path=str(tmp_path / "handoff-status.json"),
        transcript_path=str(transcript),
        transcript_root=str(tmp_path),
        requested_at=datetime.now(UTC),
    )

    key_first = endpoint._derive_idempotency_key(req)
    key_second = endpoint._derive_idempotency_key(req)

    assert key_first == key_second


def test_derive_idempotency_key_handles_missing_transcript(tmp_path):
    endpoint = HandoffJobsEndpoint(name="test")

    req = HandoffJobRequest(
        session_id="session-x",
        event="SessionEnd",
        agent_name="pepper",
        vault_root=str(tmp_path),
        handoff_path=str(tmp_path / "handoff.md"),
        handoff_status_path=str(tmp_path / "handoff-status.json"),
        transcript_path=str(tmp_path / "does-not-exist.jsonl"),
        transcript_root=str(tmp_path),
        requested_at=datetime.now(UTC),
    )

    # Must not raise; must produce a deterministic key
    key_a = endpoint._derive_idempotency_key(req)
    key_b = endpoint._derive_idempotency_key(req)
    assert key_a == key_b
    assert isinstance(key_a, str)
    assert len(key_a) == 64  # sha256 hex
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "derive_idempotency_key" -v`

Expected: 3 FAILs. Two of them fail because the current key formula doesn't include `transcript_size` (so `key_a == key_b` in the first test, breaking the inequality assertion). The "missing transcript" test passes accidentally if the current static method ignores `transcript_path` entirely — verify it actually fails by checking the test name on the failure list. Re-check after the implementation lands.

- [ ] **Step 1.3: Update `_derive_idempotency_key`**

Replace the existing method (currently at `packages/core/src/agent_core/endpoints/handoff_jobs.py:506-509`):

```python
    @staticmethod
    def _derive_idempotency_key(req: HandoffJobRequest) -> str:
        raw = f"{req.session_id}|{req.event}|{req.handoff_path}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

With:

```python
    def _derive_idempotency_key(self, req: HandoffJobRequest) -> str:
        """Derive a content-varying idempotency key.

        Includes ``transcript_size`` so real session activity produces a
        fresh key (the original ``--continue`` collision bug, #42). For
        files that no longer exist at intake time, falls back to size=-1 —
        the worker will fail loudly with FileNotFoundError downstream,
        which is the correct loud-failure behavior.
        """
        transcript_path = Path(req.transcript_path).expanduser()
        try:
            size = transcript_path.stat().st_size
        except OSError:
            size = -1
        raw = f"{req.session_id}|{req.event}|{req.handoff_path}|{size}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

The call site `self._derive_idempotency_key(job_req)` (currently at line 216 in `_post_job`) does not need to change — it was already calling via `self.`, which works whether the method is static or instance.

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "derive_idempotency_key" -v`

Expected: 3 passed.

Run the full test file: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -v`

Expected: all tests pass (existing 26 + new 3 = 29).

Run lint: `uv run ruff check packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py`

Expected: zero violations.

- [ ] **Step 1.5: Commit**

```bash
git add packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "fix(handoff): include transcript_size in idempotency key (daemon)

Adds the transcript file's st_size to _derive_idempotency_key so each
real --continue resume produces a distinct key (the previous formula
collided across resumes because session_id is reused). Falls back to
size=-1 on OSError so missing transcripts still produce a deterministic
key — the worker fails loudly via the existing FileNotFoundError path.

Converts the method from static to instance to ease future use of
self-state without re-plumbing.

Refs #42"
```

---

## Task 2: Hook stops sending `idempotency_key`

**Why next:** With the daemon now correctly deriving the key, the hook should stop short-circuiting that derivation by sending its own (still-coarse) key. After this task, the daemon's new logic kicks in for every hook-fired POST.

**Files:**
- Modify: `packages/core/src/agent_core/hooks/tools/handoff_writer.py:66` (delete the `idempotency_key` line)
- Modify: `packages/core/tests/test_handoff_writer.py` (add 1 regression test)

- [ ] **Step 2.1: Write the failing test**

Add this test inside the `TestHandoffWriter` class in `packages/core/tests/test_handoff_writer.py` (next to the existing `test_enqueues_daemon_job`):

```python
    @patch("agent_core.hooks.tools.handoff_writer.urllib.request.urlopen")
    def test_does_not_send_idempotency_key(self, mock_urlopen, tmp_path: Path):
        mock_urlopen.return_value = _FakeResponse({"job_id": "job-x", "status": "accepted"})
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("{}", encoding="utf-8")
        output = tmp_path / "handoff.md"

        tool = HandoffWriter()
        tool.execute(
            event="SessionEnd",
            hook_input={"transcript_path": str(transcript), "session_id": "session-1"},
            params={"output_path": str(output)},
        )

        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        assert "idempotency_key" not in payload
```

- [ ] **Step 2.2: Run test to verify it fails**

Run: `uv run pytest packages/core/tests/test_handoff_writer.py::TestHandoffWriter::test_does_not_send_idempotency_key -v`

Expected: FAIL with `assert "idempotency_key" not in payload` failing — the current code at `handoff_writer.py:66` still sets that field.

- [ ] **Step 2.3: Delete the `idempotency_key` line in the hook payload**

Edit `packages/core/src/agent_core/hooks/tools/handoff_writer.py`. Find this block (currently around lines 57-68):

```python
        payload = {
            "session_id": session_id,
            "event": event,
            "agent_name": agent_name,
            "vault_root": str(vault_root),
            "handoff_path": str(output_path),
            "handoff_status_path": str(status_path),
            "transcript_path": transcript_path_str,
            "requested_at": datetime.now(UTC).isoformat(),
            "idempotency_key": f"{session_id}:{event}:{output_path}",
            "context": {},
        }
```

Remove the `"idempotency_key": ...` line:

```python
        payload = {
            "session_id": session_id,
            "event": event,
            "agent_name": agent_name,
            "vault_root": str(vault_root),
            "handoff_path": str(output_path),
            "handoff_status_path": str(status_path),
            "transcript_path": transcript_path_str,
            "requested_at": datetime.now(UTC).isoformat(),
            "context": {},
        }
```

Nothing else changes in this file.

- [ ] **Step 2.4: Run targeted test + full hook test file**

Run: `uv run pytest packages/core/tests/test_handoff_writer.py -v`

Expected: all hook tests pass (existing tests + the new regression test).

Run lint: `uv run ruff check packages/core/src/agent_core/hooks/tools/handoff_writer.py packages/core/tests/test_handoff_writer.py`

Expected: zero violations.

- [ ] **Step 2.5: Commit**

```bash
git add packages/core/src/agent_core/hooks/tools/handoff_writer.py packages/core/tests/test_handoff_writer.py
git commit -m "fix(handoff): stop sending idempotency_key from hook (single source of truth)

The hook's hand-rolled key f'{session_id}:{event}:{output_path}' had
the same collision bug as the daemon's previous _derive_idempotency_key
formula. Now that the daemon derives a content-varying key (#42 part 1),
the hook should let the daemon do the work — there's no value in two
implementations and the previous one short-circuited the new derivation.

The HandoffJobRequest.idempotency_key field stays optional for direct-API
callers that want explicit control.

Refs #42"
```

---

## Task 3: End-to-end dedup integration tests

**Why next:** Tasks 1 and 2 each verified their own surface in isolation. Task 3 proves the system behavior end-to-end — that two POSTs with different transcript states produce different jobs, and two POSTs with identical state still dedupe.

**Files:**
- Modify: `packages/core/tests/test_handoff_jobs_endpoint.py` (add 2 integration tests at the end)

- [ ] **Step 3.1: Write the failing tests**

Add to `packages/core/tests/test_handoff_jobs_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_post_job_dedupes_when_transcript_unchanged(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-dedupe.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"hello"}}\n', encoding="utf-8"
    )
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
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    extract_calls = 0
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _counting_extract(self, req, transcript_text, *args, **kwargs):
                nonlocal extract_calls
                extract_calls += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _counting_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-dedupe",
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
                resp_first = await client.post(url, json=payload)
                resp_second = await client.post(url, json=payload)

            assert resp_first.status_code == 202
            assert resp_second.status_code == 202
            job_first = resp_first.json()["job_id"]
            job_second = resp_second.json()["job_id"]
            assert job_first == job_second  # dedup: identical state = same job

            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            assert extract_calls == 1  # worker ran once, not twice
        finally:
            await bus.stop()
    finally:
        await http_host.stop()


@pytest.mark.asyncio
async def test_post_job_creates_new_job_when_transcript_grows(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    transcript_root = tmp_path / "claude_projects"
    transcript_root.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_root / "session-grow.jsonl"
    transcript_path.write_text(
        '{"message":{"role":"user","content":"first"}}\n', encoding="utf-8"
    )
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
  - type: builtin.stub
    name: pepper
""",
        encoding="utf-8",
    )

    bus, http_host = await build_bus_from_config(cfg)
    assert http_host is not None
    await http_host.start()
    extract_calls = 0
    try:
        await bus.start()
        try:
            endpoint = bus._endpoints_by_name["handoff-jobs"].endpoint

            async def _counting_extract(self, req, transcript_text, *args, **kwargs):
                nonlocal extract_calls
                extract_calls += 1
                return "# Handoff\n"

            monkeypatch.setattr(type(endpoint), "_extract_handoff", _counting_extract)
            url = f"http://127.0.0.1:{http_host.port}/internal/handoff-jobs"
            payload = {
                "session_id": "session-grow",
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
                resp_first = await client.post(url, json=payload)

            # wait for first job to complete so its status becomes ready
            for _ in range(40):
                if status_path.exists():
                    state = json.loads(status_path.read_text(encoding="utf-8")).get("state")
                    if state == "ready":
                        break
                await asyncio.sleep(0.05)

            # simulate conversation activity by appending to the transcript
            with transcript_path.open("a", encoding="utf-8") as fh:
                fh.write('{"message":{"role":"assistant","content":"second"}}\n')

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp_second = await client.post(url, json=payload)

            assert resp_first.status_code == 202
            assert resp_second.status_code == 202
            job_first = resp_first.json()["job_id"]
            job_second = resp_second.json()["job_id"]
            assert job_first != job_second  # transcript grew = new job

            for _ in range(40):
                if extract_calls >= 2:
                    break
                await asyncio.sleep(0.05)

            assert extract_calls == 2  # worker ran twice
        finally:
            await bus.stop()
    finally:
        await http_host.stop()
```

- [ ] **Step 3.2: Run tests to verify they pass**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -k "dedupes_when_transcript_unchanged or creates_new_job_when_transcript_grows" -v`

Expected: 2 passed.

These should pass on the first run because the daemon already has the fix from Task 1 and the hook isn't involved (these tests POST directly with no `idempotency_key`, so the daemon derives — which is the new behavior).

If a test fails: re-check that the test does NOT include `idempotency_key` in its payload. If the test sets that field, the daemon will use it as-is and skip derivation, defeating the test.

- [ ] **Step 3.3: Run the full test file**

Run: `uv run pytest packages/core/tests/test_handoff_jobs_endpoint.py -v`

Expected: all tests pass (existing 29 from after Task 1 + new 2 = 31).

Run lint: `uv run ruff check packages/core/tests/test_handoff_jobs_endpoint.py`

Expected: zero violations.

- [ ] **Step 3.4: Commit**

```bash
git add packages/core/tests/test_handoff_jobs_endpoint.py
git commit -m "test(handoff): end-to-end dedup behavior for varying transcript size

Two integration tests prove the new key shape works through the full
HTTP intake path:
- test_post_job_dedupes_when_transcript_unchanged — two identical POSTs
  with the transcript file unchanged share a job_id and worker runs once
- test_post_job_creates_new_job_when_transcript_grows — two POSTs with
  the transcript appended between them get distinct job_ids and worker
  runs twice (the bug-fix path for #42)

Refs #42"
```

---

## Task 4: Full suite + push + PR

**Why last:** Final regression sweep across the whole core package, then push the branch and open the PR.

**Files:**
- None modified.

- [ ] **Step 4.1: Run the full core test suite**

Run: `uv run pytest packages/core/tests/ -v`

Expected: 521 passed, 2 skipped (515 from main + 6 new from this branch). Investigate any unrelated failures before declaring victory.

- [ ] **Step 4.2: Run lint**

Run: `uv run ruff check packages/core/src/agent_core/endpoints/handoff_jobs.py packages/core/src/agent_core/hooks/tools/handoff_writer.py packages/core/tests/test_handoff_jobs_endpoint.py packages/core/tests/test_handoff_writer.py`

Expected: zero violations.

- [ ] **Step 4.3: Push the branch**

Run: `git push -u origin fix/issue-42-idempotency-key`

If the push fails (auth, branch protection), report it as BLOCKED.

- [ ] **Step 4.4: Open the PR**

```bash
gh pr create --base main --title "fix(handoff): per-resume idempotency key (#42)" --body "$(cat <<'EOF'
## Summary

Fixes the second half of #35's compounding bugs. \`HandoffJobsEndpoint\`'s
idempotency key collided across \`--continue\` session resumes because
\`session_id\` + \`event\` + \`handoff_path\` are all stable across
resumes. Now the key includes \`transcript_size\` from \`os.stat()\`, so
real conversation activity always produces a fresh key and a fresh
handoff. The hook stops sending its own (still-coarse) key — the daemon
becomes the single source of truth.

This also resolves #43 by superseding it: with \`transcript_size\` in
the key, no two real handoff fires share a key, so daemon-restart
cache-loss stops being observable.

Closes #42.
Closes #43.

## Spec + Plan

- Spec: \`docs/superpowers/specs/2026-05-07-issue-42-idempotency-key-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-07-issue-42-idempotency-key-plan.md\`

## Test plan

- [x] New unit tests for \`_derive_idempotency_key\` (size varies, stable for same size, missing-file sentinel)
- [x] New integration tests for end-to-end dedup behavior (same state = same job, grown state = new job)
- [x] New hook regression test asserting \`idempotency_key\` is not in the POST payload
- [x] Existing handoff-related tests still pass
- [ ] Manual verification on Pepper: \`/exit\` her \`--continue\` session twice without daemon restart, confirm two distinct \`job_id\`s and fresh handoff content (Jeff to run after merge)

## After merge

Closes Phase 1 of the open-issues cleanup roadmap. Phase 2 (observability)
is next.
EOF
)"
```

Capture the PR URL.

- [ ] **Step 4.5: Report**

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED
- Full core test suite count
- Lint result
- Push result
- PR URL
- Any concerns

---

## Self-review checklist

**Spec coverage:** Each spec section has a corresponding task.
- `_derive_idempotency_key` change → Task 1
- Hook simplification → Task 2
- 3 unit tests → Task 1 (Step 1.1)
- 2 integration tests → Task 3 (Step 3.1)
- 1 hook regression test → Task 2 (Step 2.1)
- Resolves #43 by superseding → mentioned in PR body (Task 4 Step 4.4)
- Manual Pepper verification → mentioned in PR body as a follow-up

**Placeholder scan:** No TBDs, no "implement appropriately," no "similar to Task N." Code blocks are concrete.

**Type consistency:** `_derive_idempotency_key(self, req)` signature is consistent across Task 1 introduction and Task 3's verification. The `HandoffJobRequest` constructor calls in Task 1's tests use the same field names that the model defines. The integration tests in Task 3 use the same payload shape as the existing test patterns in the file.

**Granularity:** Each step is 2-5 minutes. Four tasks total. Each task ends with a commit (except Task 4 which ends with a push + PR).
