# Spec: hoist `JsonlAuditLog` base into core, subclass in briefs/voice/webcam (issue #404)

## Goal

Eliminate the copy-pasted JSONL audit infrastructure across `agent-core-briefs`,
`agent-core-voice`, and `agent-core-webcam` by extracting a generic
`JsonlAuditLog[E]` abstract base into `packages/core`. The three packages subclass
it and delegate the shared write/swallow/append machinery, keeping only
domain-specific `AuditEvent` fields and `_serialize` implementations.
`agent-core-inbound` stays architecturally separate. Part of epic #262 ·
Theme F #269 Track B, specified in
`docs/superpowers/specs/2026-07-16-theme-f-track-b-testing-ci-design.md` (Decision D5).

---

## Acceptance criteria

- `packages/core/src/agent_core/audit.py` exists and exports `JsonlAuditLog`.
- `JsonlAuditLog[E]` is a generic abstract base owning `write`, `_append_line`,
  and the swallow policy; `_serialize(self, event: E) -> str` is declared
  `@abstractmethod`.
- `packages/agent-core-briefs/src/agent_core_briefs/audit.py`,
  `packages/agent-core-voice/src/agent_core_voice/audit.py`, and
  `packages/agent-core-webcam/src/agent_core_webcam/audit.py` each subclass
  `JsonlAuditLog` — no `write`, `_append_line`, or swallow logic duplicated.
- `packages/agent-core-inbound/src/agent_core_inbound/audit.py` is unchanged.
- All six existing audit test files pass without modification:
  `packages/agent-core-briefs/tests/test_audit.py`,
  `packages/agent-core-voice/tests/test_audit.py`,
  `packages/agent-core-webcam/tests/test_audit.py`,
  `packages/agent-core-inbound/tests/test_audit.py`.
- `packages/core/tests/test_jsonl_audit_log.py` exists and characterizes the shared
  append/serialize/swallow path via a minimal concrete test subclass.
- `mypy --strict` is clean for `packages/core/src` (the new file is already in
  that scope).
- The refactored briefs/voice/webcam `audit.py` files are written to `--strict`
  quality (correct annotations) even though they enter `[tool.mypy] files` in B5.

---

## Approach

The shared infrastructure follows the **Template Method** pattern: `JsonlAuditLog[E]`
owns the algorithm (async → thread → append → swallow) and declares one abstract
step (`_serialize`) that each subclass fills in. Google's "make the right thing
easy" applies — centralizing the swallow policy means a future change (log level,
metric increment) flows to all three packages automatically.

**Where the base lives.** `packages/core/src/agent_core/audit.py` — a sibling of
`agent_core/mcp_audit/writer.py`, which already exhibits a near-identical shape
(async write, `asyncio.to_thread`, `_append_line` staticmethod, swallow at WARNING).
The base is **not** re-exported from `agent_core/__init__.py`; importers use the
full path `from agent_core.audit import JsonlAuditLog`, consistent with the
`mcp_audit` convention.

**Logger name preservation — the one non-obvious behavioral constraint.** All three
packages name their warning logger after their own module (e.g.,
`agent_core_briefs.audit`). The briefs `test_write_swallows_disk_failure` test
explicitly filters by `caplog.at_level("WARNING", logger="agent_core_briefs.audit")`.
If the base class used `logging.getLogger(__name__)` it would produce
`agent_core.audit` and break that assertion. The base must therefore resolve the
logger dynamically at construction time:

```python
self._log = logging.getLogger(self.__class__.__module__)
```

This yields the *subclass's* module name (e.g. `agent_core_briefs.audit`) for every
warning emitted during `write`, preserving the per-package logger hierarchy.
The error message `f"{self.__class__.__module__}: write failed for {self._path}: {exc}"`
matches what the three packages currently produce verbatim.

**`_append_line` unification.** `briefs.AuditLog._append_line` uses two `write()`
calls (`handle.write(line); handle.write("\n")`); voice and webcam use one
(`handle.write(line + "\n")`). The base adopts the single-call form, which is
functionally identical and correctly preserves the POSIX O_APPEND atomicity
guarantee commented in the voice/webcam implementations.

**No `pyproject.toml` changes.** All three packages already declare
`agent-core>=0.7,<0.8`, so `from agent_core.audit import JsonlAuditLog` works
today without touching any manifest.

**`_serialize` as an instance method.** In the original implementations `_serialize`
is a `@staticmethod`. Making it `@abstractmethod` in the base requires it to be an
instance method (mypy `--strict` flags a static override of an abstract instance
method). The Worker drops the `@staticmethod` decorator and adds `self` to all
three `_serialize` overrides. The call site `self._serialize(event)` in `write()`
is unchanged.

**Inbound stays separate.** Its design is synchronous, injectable-clock, and uses
named `record_allow`/`record_deny` semantics rather than a generic `write`. The
ticket leaves inbound untouched.

---

## Sub-requests (topologically sorted)

1. **Create `packages/core/src/agent_core/audit.py`.**
   Content skeleton (Worker fills bodies):

   ```python
   """Generic append-only JSONL audit log base.

   Subclasses implement ``_serialize`` to convert a domain event to a JSON
   string. The base owns the async write, thread offload, POSIX-atomic
   append, and swallow policy.
   """
   from __future__ import annotations

   import asyncio
   import logging
   import sys
   from abc import ABC, abstractmethod
   from pathlib import Path
   from typing import Generic, TypeVar

   E = TypeVar("E")


   class JsonlAuditLog(ABC, Generic[E]):
       def __init__(self, path: Path) -> None:
           self._path = Path(path)
           self._log = logging.getLogger(self.__class__.__module__)

       @property
       def path(self) -> Path:
           return self._path

       @abstractmethod
       def _serialize(self, event: E) -> str: ...

       @staticmethod
       def _append_line(path: Path, line: str) -> None:
           path.parent.mkdir(parents=True, exist_ok=True)
           with path.open("a", encoding="utf-8") as handle:
               handle.write(line + "\n")

       async def write(self, event: E) -> None:
           try:
               line = self._serialize(event)
               await asyncio.to_thread(self._append_line, self._path, line)
           except Exception as exc:
               msg = (
                   f"{self.__class__.__module__}: write failed "
                   f"for {self._path}: {exc}"
               )
               self._log.warning(msg)
               print(msg, file=sys.stderr)


   __all__ = ["JsonlAuditLog"]
   ```

2. **Create `packages/core/tests/test_jsonl_audit_log.py`** — characterization
   tests for the shared machinery. Use a minimal concrete subclass:

   ```python
   import json
   from dataclasses import dataclass
   from pathlib import Path
   import pytest
   from agent_core.audit import JsonlAuditLog

   @dataclass(frozen=True)
   class _Evt:
       msg: str

   class _Log(JsonlAuditLog[_Evt]):
       def _serialize(self, event: _Evt) -> str:
           return json.dumps({"msg": event.msg}, ensure_ascii=False)
   ```

   Required test cases (all `@pytest.mark.asyncio`):
   - `test_append_line_creates_parent_dirs(tmp_path)` — call `_Log._append_line`
     on a nested path; assert file exists.
   - `test_append_line_appends_not_truncates(tmp_path)` — two calls produce
     two lines.
   - `test_write_produces_readable_jsonl(tmp_path)` — single write; assert
     `json.loads(line)["msg"] == "hello"`.
   - `test_write_swallows_disk_failure(tmp_path)` — blocker-file technique;
     assert no exception raised, file not created.
   - `test_write_logs_warning_on_failure(tmp_path, caplog)` — same blocker;
     assert a `WARNING` record appears in `caplog.records`.
   - `test_path_property_round_trips_constructor_arg(tmp_path)` — sync,
     non-async; assert `_Log(p).path == p`.

3. **Refactor `packages/agent-core-briefs/src/agent_core_briefs/audit.py`.**
   - Add `from agent_core.audit import JsonlAuditLog`.
   - Change `class AuditLog(JsonlAuditLog[AuditEvent]):`.
   - Remove `write`, `_append_line` (now inherited).
   - Convert `_serialize` from `@staticmethod def _serialize(event: AuditEvent)`
     to `def _serialize(self, event: AuditEvent) -> str:` — body unchanged
     (`json.dumps(payload, default=str, ensure_ascii=False)`).
   - Keep `default_path()` static method and `__all__` unchanged.
   - The `path` property is now inherited; remove the local definition.
   - The `__init__` delegates: `def __init__(self, path: Path) -> None: super().__init__(path)`.
   - Remove `import asyncio`, `import sys`, `import logging` (no longer used
     directly — the base class owns logging; the module-level
     `log = logging.getLogger(__name__)` is also dropped); keep
     `import json`, `from datetime import datetime`,
     `from pathlib import Path`, `from typing import Any`.
   - Drop module-level `log = logging.getLogger(__name__)` (base owns logging now).

4. **Refactor `packages/agent-core-voice/src/agent_core_voice/audit.py`.**
   Same pattern as #3. Voice has no `default_path` — preserve that omission.
   Voice's `_serialize` uses `json.dumps(payload, ensure_ascii=False)` (no
   `default=str`) — keep that exactly. Remove `import asyncio`, `import sys`,
   and `import logging` (no longer used directly); drop the module-level logger
   assignment. Keep `import json`, `from pathlib import Path`, and any other
   imports voice uses directly.

5. **Refactor `packages/agent-core-webcam/src/agent_core_webcam/audit.py`.**
   Same pattern as #3. Webcam keeps `default_path(endpoint_name: str) -> Path`
   as a `@staticmethod`. Webcam's `_serialize` uses
   `json.dumps(payload, default=str, ensure_ascii=False)`. Remove
   `import asyncio`, `import sys`, and `import logging` (no longer used
   directly); drop the module-level logger assignment. Keep `import json`,
   `from pathlib import Path`, and any other imports webcam uses directly.

6. **Verify.** Run:
   ```bash
   uv run pytest \
     packages/core/tests/test_jsonl_audit_log.py \
     packages/agent-core-briefs/tests/test_audit.py \
     packages/agent-core-voice/tests/test_audit.py \
     packages/agent-core-webcam/tests/test_audit.py \
     packages/agent-core-inbound/tests/test_audit.py \
     --no-cov -n0 -v
   ```
   All must be green. Then confirm `--strict` on the new file:
   ```bash
   uv run mypy --strict packages/core/src
   ```
   Must exit 0 with no errors in `agent_core/audit.py`.

---

## File-level changes

| File | Action | What changes |
|---|---|---|
| `packages/core/src/agent_core/audit.py` | **Create** | `JsonlAuditLog[E]` ABC with `write`, `_append_line`, `_serialize` |
| `packages/core/tests/test_jsonl_audit_log.py` | **Create** | Characterization tests via `_Log` test subclass |
| `packages/agent-core-briefs/src/agent_core_briefs/audit.py` | **Modify** | Subclass `JsonlAuditLog`; remove dup `write`, `_append_line`; `_serialize` → instance method |
| `packages/agent-core-voice/src/agent_core_voice/audit.py` | **Modify** | Same; no `default_path` |
| `packages/agent-core-webcam/src/agent_core_webcam/audit.py` | **Modify** | Same; preserve `default_path(endpoint_name)` |

No changes to: `pyproject.toml` files, `agent_core/__init__.py`, CI workflows,
`justfile`, or `agent-core-inbound`.

---

## Alternatives considered

1. **Strategy pattern — inject serializer callable at `__init__`.**
   `JsonlAuditLog.__init__(path, *, serializer: Callable[[E], str])` with each
   subclass passing `serializer=_serialize_event` to `super().__init__`. More
   flexible (swappable serializer), but subclass `__init__` grows boilerplate for
   zero practical benefit — serializers are never swapped at runtime. Template
   Method is simpler and maps cleanly onto the existing class structure. Ruled out.

2. **Module-level `_append_line` function in core, no base class.**
   Export a standalone `append_jsonl_line(path, line)` helper from `agent_core`;
   each package imports it in its own `AuditLog`. Eliminates the `_append_line`
   duplication but leaves `write`, the swallow policy, `path` property, and
   `__init__` duplicated across three classes. The issue explicitly asks for a
   base that owns the swallow policy. Ruled out.

3. **Merge all three into one multi-domain `AuditLog` with a union event type.**
   A single class accepting `BriefsAuditEvent | VoiceAuditEvent | WebcamAuditEvent`.
   Couples unrelated domain schemas in core, violates SRP, and requires core to
   know about every downstream package's event shape. Ruled out immediately.

---

## Open questions

None. All four `audit.py` files and all associated test files were read directly.
The logger-name constraint was confirmed by reading
`packages/agent-core-briefs/tests/test_audit.py:117`
(`caplog.at_level("WARNING", logger="agent_core_briefs.audit")`). All three
packages already declare `agent-core>=0.7,<0.8`; no manifest changes are needed.

---

## Out of scope

- Adding `agent-core-briefs`, `agent-core-voice`, `agent-core-webcam` to
  `[tool.mypy] files` — that is ticket B5, which sequences after B4 to avoid
  re-annotating churned audit code.
- Modifying `agent-core-inbound/src/agent_core_inbound/audit.py` to call
  `JsonlAuditLog._append_line`. The issue notes inbound "may adopt `_append_line`
  only" as an optional future step, not a B4 requirement.
- Exposing `JsonlAuditLog` via `agent_core/__init__.py`. Importers use the full
  module path, consistent with `mcp_audit` usage.
- Changes to `MCPAuditWriter` in `agent_core/mcp_audit/` — a different concern
  (daemon-wide tool-call auditing with daily rotation and an `asyncio.Lock`).
- Any changes to the MCP audit test suite or other core tests.
