# Spec: replace hardcoded `__version__` with `importlib.metadata` lookup (issue #312)

## Goal

`packages/core/src/agent_core/__init__.py` hardcodes `__version__ = "0.1.0"`, which
drifts from the git-tag-driven dynamic version produced by `uv-dynamic-versioning`.
This spec replaces the hardcoded string with `importlib.metadata.version("agent-core")`
so the runtime attribute always reflects the installed distribution version.

Design authority: [distribution & versioning design](https://github.com/jeffrichley/agent_core/blob/main/docs/superpowers/specs/2026-07-14-distribution-versioning-design.md), C1-4 of 4.
Issue: https://github.com/jeffrichley/agent_core/issues/312

## Acceptance criteria

- `agent_core.__version__ == importlib.metadata.version("agent-core")` is `True` in the
  installed (or editable-installed) package.
- No hardcoded version string (e.g. `"0.1.0"`) remains in
  `packages/core/src/agent_core/__init__.py`.
- A fast-lane unit test (`packages/core/tests/test_version.py`) asserts the invariant
  and passes in CI (`just test-fast`).

## Approach

No GoF pattern applies — this is a straightforward data-source swap. Principle: **single
source of truth** (the installed distribution metadata, driven by `uv-dynamic-versioning`
from git tags).

Python's stdlib `importlib.metadata.version()` (available since Python 3.8, always
present in 3.12+) is the canonical mechanism endorsed by PEP 566 and the Python
Packaging Authority for exposing the installed distribution version at runtime. No
third-party dependency is needed.

The standard defensive idiom wraps the call in `try/except PackageNotFoundError` to
avoid an `ImportError`-style crash when the package is somehow queried in a completely
un-installed source tree (rare with `uv sync --editable`, but defensive coding costs
nothing here). The fallback string `"unknown"` is NOT a version number and does not
violate "no hardcoded version string remains".

`importlib.metadata` is already in the stdlib for Python 3.12 (the project's minimum);
no new dependency needs adding to `packages/core/pyproject.toml`.

The test (`test_version.py`) simply imports both `agent_core` and
`importlib.metadata.version` and asserts equality. Because `uv sync` in CI creates a
proper editable install with metadata, `version("agent-core")` will resolve correctly.
The test is small (< 1 s, no I/O) and must NOT be marked `@pytest.mark.slow`.

## Sub-requests (topologically sorted)

1. **Edit `packages/core/src/agent_core/__init__.py`** — replace line 14
   (`__version__ = "0.1.0"`) with the `importlib.metadata` lookup:

   ```python
   from importlib.metadata import PackageNotFoundError, version

   try:
       __version__: str = version("agent-core")
   except PackageNotFoundError:  # pragma: no cover
       __version__ = "unknown"
   ```

   The `from importlib.metadata import …` lines go immediately after the module
   docstring (before any other top-level code). The `pragma: no cover` suppresses
   branch-coverage noise for the fallback path that only fires in un-installed
   source trees.

2. **Create `packages/core/tests/test_version.py`** — assert the invariant:

   ```python
   """Fast: __version__ is backed by importlib.metadata, not a hardcoded string."""
   from __future__ import annotations

   from importlib.metadata import version


   def test_version_matches_importlib_metadata() -> None:
       import agent_core

       assert agent_core.__version__ == version("agent-core")


   def test_version_is_not_hardcoded_sentinel() -> None:
       import agent_core

       # The old hardcoded value was "0.1.0".  If dynamic versioning is working,
       # the installed version will be something else (or at minimum not the
       # stale sentinel).  This guard catches accidental rollback.
       assert agent_core.__version__ != "0.1.0"
   ```

   Both tests are small, import-only, and run in the default (`not slow`) fast lane.

## File-level changes

| File | Change |
|---|---|
| `packages/core/src/agent_core/__init__.py` | Replace `__version__ = "0.1.0"` (line 14) with `importlib.metadata.version("agent-core")` wrapped in `try/except PackageNotFoundError` |
| `packages/core/tests/test_version.py` | **New file** — two fast-lane assertions: version matches importlib.metadata, version is not the stale `"0.1.0"` sentinel |

## Alternatives considered

1. **Keep the hardcoded string and update it manually on each release**: The string will
   drift again as soon as anyone cuts a release tag and forgets to update
   `__init__.py`. Ruled out: the problem being fixed.

2. **Read the version from `pyproject.toml` at runtime**: Requires parsing a TOML file
   relative to `__file__` — fragile under editable installs, wheel installs, and
   `zipimport`. `importlib.metadata` is the standard, zero-fragility alternative.
   Ruled out: unnecessary complexity.

## Open questions

None.

## Out of scope

- Applying the same `importlib.metadata` pattern to sibling packages
  (`agent-core-channel`, `agent-core-voice`, etc.) — none of them expose a
  `__version__` today; that can be a follow-up if those packages grow a public
  version attribute.
- Any change to `pyproject.toml` build config — dynamic versioning is already
  wired (`uv-dynamic-versioning`); this ticket is purely a runtime fix.
- The `test_dynamic_versioning.py` slow tests (real `uv build` round-trips) — they
  test the build artefact version, not the runtime attribute; leave untouched.
