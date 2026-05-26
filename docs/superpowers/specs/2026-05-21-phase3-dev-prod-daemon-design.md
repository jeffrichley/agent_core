# Phase 3 — Dev/Prod Daemon Instance-Parameterization: Design

**Date:** 2026-05-21
**Status:** brainstormed; pending implementation plan
**Relationship to the maturity spec:** Implements the `Phase 3 —
Dev/prod instance-parameterization` section of
`docs/superpowers/specs/2026-05-18-agent-core-maturity-design.md`. Where
they differ, **this document wins**. The umbrella spec predates Phase
2.5; it described the dev instance as having "a separate venv,
install-stamp" (i.e. installed like prod). Phase 2.5 removed
source-based install entirely — `daemon install` now installs *only*
release artifacts. This spec therefore **corrects** the umbrella spec:
the dev instance has **no venv of its own** and **no install** — it
runs editable from the workspace `.venv`.

---

## 1. Goal

Let Jeff iterate on daemon code without bouncing the production daemon
that Pepper and Wren depend on. Today there is a single daemon (one
home dir, one port, port 8789, home `~/.agent-core/`). Phase 3 adds a
second, fully isolated **dev** instance so the daemon can be restarted,
crashed, and rebuilt freely on port 8788 while live Pepper + Wren keep
running on 8789.

Out of scope: Phase 4 (Windows Task Scheduler auto-start — targets the
prod instance, depends on this phase); containerization.

## 2. Instance resolution

Every `agent-core daemon` subcommand resolves a single **instance**
(`prod` or `dev`) that determines its home dir and daemon python.

### 2.1 Precedence (most → least specific)

1. **`AGENT_CORE_HOME` env var** → if set, used directly as the home
   dir, bypassing the instance→home mapping entirely. This is the
   existing test escape hatch (tests point it at a tmp dir); unchanged.
2. **`--instance {prod|dev}` flag** → explicit per-invocation selection.
3. **`AGENT_CORE_INSTANCE` env var** → `prod` or `dev`.
4. **Default** → `prod`.

When `AGENT_CORE_HOME` is set, the resolved instance still matters for
the *daemon python* decision (§2.3) and the *default port* (§2.2), but
the home dir is the override value. In practice `AGENT_CORE_HOME` is
only set by tests, which also control the python, so this composition
is well-defined.

### 2.2 Instance → home + port

| | prod | dev |
|---|---|---|
| Home dir | `~/.agent-core/` | `~/.agent-core-dev/` |
| Bus port (in that instance's `agent_core.yaml`) | 8789 | 8788 |
| `daemon.pid` / `daemon.log` / `agent_core.yaml` / `bus.sqlite` / `endpoints.d/` / scheduler state | under prod home | under dev home |
| Install stamp | `~/.agent-core/.daemon-install-stamp.json` | none — dev is not installed |

The port lives in each instance's `agent_core.yaml` (the bus reads
`http.bind_port` from config). `default_port(instance)` is used only by
`daemon init` when scaffolding a fresh config.

### 2.3 Instance → daemon python

- **prod** → `~/.agent-core/.venv/Scripts/python.exe` (Windows) /
  `bin/python` (POSIX) — the release-installed venv from Phase 2.5.
- **dev** → the **workspace `.venv`**, resolved via
  `find_workspace_root(Path.cwd()) / ".venv" / "Scripts" / "python.exe"`
  (the `find_workspace_root` utility was deliberately kept in
  `daemon/install.py` through Phase 2.5 for exactly this kind of use).
  agent_core is installed editable there, so source edits in the repo
  are picked up by the dev daemon on its next restart.

  Resolving via `find_workspace_root` (rather than `sys.executable`)
  means dev commands must be invoked with the current directory inside
  the agent_core repo tree. That is the normal condition during
  development. If the workspace root cannot be found, `daemon
  start/refresh --instance dev` fails fast with a clear message.

## 3. CLI command surface

Every `agent-core daemon` subcommand gains an `--instance {prod|dev}`
Typer option (default unset → resolves per §2.1).

| Command | prod behavior | dev behavior |
|---|---|---|
| `start` | spawn the bus from the prod venv; prod home; reads prod `agent_core.yaml` (port 8789) | spawn the bus from the **workspace `.venv`**; dev home; reads dev `agent_core.yaml` (port 8788) |
| `stop` | kill the prod daemon (prod pid file) | kill the dev daemon (dev pid file) |
| `status` | liveness + PID + `running from` + install stamp + log tail | liveness + PID + `running from` + log tail — **no install-stamp section** (an editable dev daemon has no stamp) |
| `install` / `install --release vX.Y.Z` | install release artifacts into the prod venv | **exit non-zero** with: *"dev runs editable from the workspace `.venv`; no install needed — just `agent-core daemon start --instance dev`"* |
| `refresh` | stop → install latest release → start | **stop → start** (a plain bounce; no install step) |
| `init` | scaffold `~/.agent-core/agent_core.yaml` (port 8789); refuse to overwrite without `--force` | scaffold `~/.agent-core-dev/agent_core.yaml` (port 8788); refuse to overwrite without `--force` — **new command** |

Two notable per-instance behaviors:

- **`refresh --instance dev` is the everyday dev loop.** Edit daemon
  code in the repo → `agent-core daemon refresh --instance dev` → the
  dev bus restarts running the new code. No install, no artifacts.
- **`status --instance dev` omits the install stamp.** The stamp
  records "what release is installed" — meaningless for an editable
  dev daemon. Showing the workspace `git describe` instead is optional
  polish, not required for Phase 3.

### 3.1 `daemon init` config scaffold

`init` generates a **minimal** `agent_core.yaml`:

```yaml
bus:
  storage_path: <home>/bus.sqlite

http:
  bind_host: 127.0.0.1
  bind_port: <8789 prod | 8788 dev>

endpoints:
  - type: builtin.stub
    name: stub
```

Rationale for minimal (not a copy of prod's full endpoint set): the dev
daemon exists to exercise daemon *plumbing*. Specific endpoints are
added when they are being tested. Mirroring prod's endpoints is a
manual copy, out of scope.

`init` refuses to overwrite an existing `agent_core.yaml` unless
`--force` is passed (prevents clobbering the live prod config).

## 4. Code structure

Follows the project's pure/impure convention (`build_uv_sync_command`
pure / `run_install` impure).

### 4.1 New — `packages/core/src/agent_core/daemon/instance.py` (pure)

- `Instance` — a `StrEnum` with members `PROD` and `DEV`.
- `resolve_instance(*, flag: str | None, env: str | None) -> Instance`
  — applies §2.1 precedence (flag > env > default `prod`). Raises a
  clear `ValueError` on an unrecognized value.
- `home_for(instance: Instance) -> Path` — maps to `~/.agent-core/` or
  `~/.agent-core-dev/`. Honors the `AGENT_CORE_HOME` escape hatch: if
  that env var is set, returns it directly.
- `default_port(instance: Instance) -> int` — 8789 / 8788.

No I/O → fully unit-testable.

### 4.2 New — `packages/core/src/agent_core/daemon/config_template.py` (pure)

- `build_default_config(*, instance: Instance, home: Path) -> str` —
  returns the minimal `agent_core.yaml` text (§3.1) with the correct
  port and a home-correct `storage_path`.

### 4.3 Modified — `packages/core/src/agent_core/daemon/cli.py` (impure wiring)

- Every command gains an `--instance` option.
- A per-invocation helper resolves the instance once and derives home +
  daemon python from it.
- `_home()` → instance-aware (delegates to `instance.home_for`).
- `_daemon_python()` → instance-aware: prod = prod-home venv; dev =
  workspace `.venv` via `find_workspace_root`.
- `install` / `refresh` → branch on instance (dev `install` errors; dev
  `refresh` is stop→start).
- New `init` command → calls `build_default_config`, writes it, refuses
  to clobber without `--force`.

### 4.4 Modified — `packages/core/src/agent_core/daemon/install.py`

No behavior change. `find_workspace_root` (kept through Phase 2.5) is
now also consumed by `cli.py` for dev-python resolution. `InstallStamp`
is written only by prod installs.

## 5. Safety invariant

Because dev's home dir, port, pid file, state DB, scheduler jobs,
`endpoints.d/`, log, and daemon python are all disjoint from prod's, a
dev `start` / `stop` / `refresh` / crash physically cannot touch prod's
venv, port, or state. Live Pepper + Wren keep running on 8789 while
iteration happens on 8788.

## 6. Backward compatibility

With no `--instance` flag and no `AGENT_CORE_INSTANCE` env var, every
command resolves to `prod` with today's exact paths and port 8789.
Existing Pepper/Wren behavior is unchanged; the dev instance is purely
additive. No migration, no config change for the live prod daemon.

## 7. Testing strategy

All fast (the Phase 1 `check` gate) except one coexistence test.

- **Instance resolution** — precedence matrix: `--instance` flag beats
  `AGENT_CORE_INSTANCE` env beats default `prod`; unrecognized value
  raises.
- **Home + port mapping** — `home_for(PROD/DEV)`, `default_port(PROD/DEV)`;
  `AGENT_CORE_HOME` override returns its value directly.
- **`_daemon_python` per instance** — prod resolves to the prod-home
  venv path; dev resolves to the workspace `.venv` path.
- **`build_default_config`** — emits parseable YAML with the correct
  `bind_port` and home-correct `storage_path` for each instance.
- **`init`** — writes the config; refuses to overwrite an existing
  config without `--force`; succeeds with `--force`.
- **`install --instance dev`** — exits non-zero with the "no install
  needed" message; never touches a venv.
- **`refresh --instance dev`** — invokes `stop` then `start`, never
  `install`.
- **Backward compatibility** — with no `--instance` and no env, the
  resolved home / port / pid path are byte-identical to today's prod.
- **Coexistence (one `slow`-marked integration test)** — start a prod
  and a dev daemon simultaneously (using `AGENT_CORE_HOME` tmp dirs +
  distinct ports), assert both are alive and independently bound, stop
  each without disturbing the other.

## 8. Rollout

1. Phase 3 PR (instance.py + config_template.py + cli.py wiring + `init`
   command + tests + a `docs/setup/daemon.md` update describing the
   dev instance and the `refresh --instance dev` loop) → passes
   `phase1-main-gate` → squash-merged via the gate.
2. After merge: release-please cuts the next version (whatever the
   commits warrant). Deploy to the prod box with `agent-core daemon
   refresh` as usual.
3. One-time dev setup on the box: `agent-core daemon init --instance
   dev`, then `agent-core daemon start --instance dev`.
4. Phase 4 (Windows auto-start, prod instance) becomes unblocked.

## 9. Open follow-ups (not Phase 3)

- **Issue #107** — release-please ↔ CI automation gap (PAT / GitHub
  App). Independent of Phase 3; both can proceed in parallel.
- `status --instance dev` showing `git describe` of the workspace —
  optional polish, deferred.
