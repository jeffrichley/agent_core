# agent-core — "Get to World-Class" Evaluation

**Date:** 2026-07-13
**Scope:** the `agent-core` framework only (the message bus + daemon + pluggable endpoints + the MCP sidecars beings use to reach the bus + the hatchery). **Explicitly excludes foreman** (a separate project).
**Method:** a live ground-truth map of the running system, then parallel read-only code audits across 11 dimensions, synthesized here into a prioritized, evidence-cited gap list clustered into design-epic themes.
**Purpose:** the "what stands between us and world-class" list. A separate design session tackles the themes; this doc is the input to it.

Priority key: **P0** = active risk (can lose data, take the system down, or leak secrets) **or** a hard blocker to the world-class / multi-being goal. **P1** = important hardening. **P2** = polish. Effort: **S/M/L**.

---

## Executive summary

The **core is genuinely well-built**: a durable at-least-once SQLite mailbox (WAL, in-flight-timeout requeue, TTL/DLQ sweeps, tight file perms, bounded per-endpoint mailboxes), a clean **pluggable-endpoint design** (endpoints register by `type:` string), strong test discipline on the pure-Python core (real HTTP/MCP/SQLite/HMAC round-trips, strict fakes that refuse shapes the real lib refuses, Ubuntu+Windows CI with coverage + patch gates), constant-time HMAC on the inbound webhook, server-stamped sender identity, and a secret-safe MCP audit trail. This is a solid foundation — the goal is to raise the layers *above* the store to the same bar.

**The gap to world-class is concentrated in six themes.** The system is currently a *single-machine, single-operator, mostly-Windows* deployment for two beings on one box — and the recovery/portability/multi-tenant/security/observability layers reflect that. Recurring root causes:

1. **No supervision above the store.** Boot is all-or-nothing; post-boot endpoints run unsupervised on one shared event loop; an uncontained exception kills every being at once (the 2026-07-09 outage class is structurally open). Restart exists only on Windows and gives up after 3 tries.
2. **Single-machine lifecycle.** Not on PyPI; autostart is Windows-Task-Scheduler-only; the daemon + all beings' MCP sidecars share one mutable, version-stamped venv; each being's `.mcp.json` hardcodes an absolute interpreter path. Config has drifted (live task ≠ the tool's task; 0.6.1 manifest vs 0.7.0 live; orphaned venvs; a stale launcher pointing at a dead venv).
3. **Co-tenancy, not multi-tenancy.** One monolithic daemon/config/port hosts all beings; the hatchery bootstraps a being's *vault* but never generates the `.mcp.json` that wires it, and doesn't reload the daemon — so a "successful" hatch isn't actually live.
4. **Secrets & local-surface security.** Vault master password sits plaintext next to the vault it unlocks; secrets print to stdout and are inherited by every subprocess env; the bus MCP surface has zero auth (loopback is the only control); the audit trail isn't tamper-evident.
5. **Operability blind spots.** Plain-text unbounded logs, no built-in health/degraded-state detection (resilience is outsourced to an external scheduler ping), no `/healthz`/metrics; no backup/checkpoint/retention/corruption-recovery for the two SQLite stores.
6. **Adoption story absent.** No from-scratch getting-started, no architecture overview, docs assume the reader is Wren; a few quality debts (release-gate never runs in CI, a real process leak, uneven mypy/test coverage, a 2033-line god-module) would bite a hardening effort.

**Overall maturity:** *Strong core, early-stage system.* Excellent as a bespoke two-being deployment; not yet a framework a stranger could adopt, nor one that self-heals unattended cross-platform.

---

## Dimension scorecard

| # | Dimension | Level | One-line |
|---|-----------|-------|----------|
| 1 | Reliability & resilience | **Early** | Great store; no supervision above it — boot fail-fast, shared-loop no-isolation, Win-only capped restart. |
| 2 | Portability | **Early** | Portable *core* (psutil, sys.platform branches, Win+Linux CI); Windows-first *lifecycle* (autostart, no macOS). |
| 3 | Install / distribution / upgrade | **Early** | No PyPI, cu130-only + hashless deployed reqs, venv sprawl, stale 0.6.1 manifest, no self-healing install. |
| 4 | Multi-tenancy / hatchery | **Early** | Thoughtful vault bootstrapper; stops before runtime isolation — no `.mcp.json` gen, shared venv, monolith config. |
| 5 | Security | **Developing** | Solid transport hygiene; weak secret-at-rest + secret-in-env + unauthenticated local MCP surface. |
| 6 | Observability | **Early** | Plain unbounded logs, PID-only health, no degraded detection / `/healthz` / metrics; good MCP audit though. |
| 7 | Config management | **Early** | Monolith config, no schema validation (raw dict.get), drift + dated `.bak` copies. |
| 8 | Testing & CI | **Strong (core)** | Real-boundary tests + strict fakes + dual-OS gates; release-gate never runs in CI, slow tests leak. |
| 9 | Data / persistence | **Developing** | Good crash-recovery primitives; no backup/checkpoint/retention/corruption-recovery/versioned migrations. |
| 10 | Code quality / tech-debt | **Strong** | Low debt, clean markers; two god-modules + 4× audit.py dup + partial mypy/test coverage. |
| 11 | Docs / onboarding | **Early (adopter)** | World-class *operator* runbook for Wren; ~zero *adopter* onboarding, no architecture doc, stale structure map. |

---

## Theme 0 — Immediate landmine defusing (today, no design needed)

Pure risk, zero design. Safe to do independent of everything below.

- **[P0] Delete the dead `~/.agent-core/.venv` (0.6.1, broken `pydantic_core`).** Nothing live uses it; it's the corpse the incident revived. — S
- **[P0] Fix or remove `~/.local/bin/agent-core-start.sh`** — it hardcodes the dead `.venv` path; the "obvious" restart command revives the corpse. — S
- **[P1] Kill the 4 stale `_backend_server.py` processes** (PIDs bound to ports 54680/58839 since 6/22 — confirmed live), from the workspace `.venv`; a non-tree-killing test teardown stranded them. — S
- **[P1] Reconcile the scheduled task:** the live `AgentCoreDaemon` is a hand-made task the tool doesn't manage (the tool installs `agent-core-daemon-prod`). Decide the one canonical task and confirm it points at the live venv. — S

---

## Theme A — Runtime resilience & supervision  *(dimension 1; the #260 core)*

- **[P0] No per-endpoint failure isolation; uncontained exceptions kill the whole daemon.** Endpoints run unsupervised on one shared event loop (`bus/cli.py:97-106` creates only sweep tasks then `await stop_event.wait()`; `bus/core.py:124-146` keeps no task registry/health). One endpoint's unhandled `anyio.ClosedResourceError`/gateway error takes down scheduler+inbound+briefs+MCP together — the 2026-07-09 class. — M
- **[P0] Boot is all-or-nothing fail-fast.** `bus/core.py:130-144` — any endpoint's `start()` throwing tears down all started endpoints and exits the process. A voice model-load failure or a scheduler DB lock at boot kills healthy endpoints too. World-class degrades: start what can, quarantine what can't, stay up, surface the degraded one. — M
- **[P0] Blocking work in `deliver()`/`__init__` freezes the shared loop.** Dispatch is awaited synchronously per envelope (`core.py:217-218`); VoiceEndpoint constructs the TTS backend synchronously in `__init__` (inferred GPU/model load on the boot thread). Any endpoint doing sync CPU/IO without `to_thread` stalls sweeps + every other endpoint. Needs an offload/worker boundary. — M
- **[P0] Cross-platform supervision absent; restart is Windows-only & capped at 3.** `daemon/cli.py:157-165` spawns the bus and returns with no watchdog; the only auto-restart is Windows Task Scheduler XML `RestartOnFailure PT1M ×3` (`autostart.py:57-60`). POSIX has none; three crashes then permanent death. Needs an unconditional backoff supervisor (in-process watchdog and/or systemd `Restart=always` / launchd `KeepAlive`). — M
- **[P1] Redelivery sweep re-dispatches inline** (`core.py:281 await self._dispatch(env)`) — one slow/stuck endpoint stalls redelivery for all mail. — M
- **[P1] No delivery retry backoff.** `persistence.py:197-203 requeue()` resets to pending with no delay; a fast-failing endpoint burns all `max_delivery_attempts` (5) instantly then dead-letters; non-`EndpointUnavailable` errors dead-letter on first failure. Needs exponential backoff + jitter + transient-vs-terminal distinction. — M
- **[P1] Endpoints ack transient failures instead of nacking** (inbound `deliver()` acks all paths except `_handle is None`; discord acks on 429/5xx) — recoverable messages silently dropped instead of requeued. — M
- **[P1] Fire-and-forget tasks leak past `stop()`; exceptions swallowed** (inbound `_bus_publish_adapter` untracked `create_task`; voice synthesis tasks untracked; discord typing/reaction tasks). Contrast `handoff_jobs`/`claude_code_mcp` which track+cancel correctly. — M
- **[P1] Unbounded in-endpoint queues defeat the bus's own backpressure** (`handoff_jobs` `asyncio.Queue()` no maxsize + always-202; voice one task/request no semaphore). — M
- **[P2] `delivery_count` increments before `deliver()`** so a crash mid-delivery still burns an attempt (`core.py:216` vs `:218`). — S
- **[P2] Graceful shutdown doesn't drain in-flight** work (`bus.stop()` just stops endpoints; in-flight `deliver()`s rely on next-boot redelivery). — M

---

## Theme B — Portable install & lifecycle  *(dimensions 2, 3; the venv sprawl)*

- **[P0] Not published to PyPI — no `uvx`/`pipx`/`uv tool install` path.** No pypi/twine/trusted-publisher anywhere in `.github/`; `release.yml` only uploads wheels to the GitHub Release; README install is `uv sync` (clone-only). An adopter cannot acquire it without cloning the monorepo. — M
- **[P0] Each being's `.mcp.json` hardcodes an absolute, version-stamped venv interpreter** (`C:\Users\jeffr\.agent-core\.venv-v0.7.0\Scripts\python.exe`, ×3 sidecars ×2 beings). Embeds username+OS+version; a venv bump strands every being; not reproducible on a second machine. World-class ships a stable launcher shim (the `agent-core-busproxy` console script exists) or resolves at runtime. — M
- **[P0] Autostart is Windows-Task-Scheduler-only; no launchd/systemd.** `daemon/cli.py:408-410,453-455` hard-exits install on non-win32; no POSIX boot-persistence generator exists. — M
- **[P1] Daemon + all beings' sidecars share ONE mutable venv** (`.venv-v0.7.0`) — no per-being version pinning; upgrades are all-or-nothing across tenants; the in-place-upgrade `.pyd`-lock hazard from the incident. — M
- **[P1] Deployed daemon requirements are cu130-GPU-only and hash-less.** `release.py` hardcodes `--extra-index-url=.../whl/cu130 --index-strategy=unsafe-best-match` for *every* install; `uv export --no-hashes`. So every install pulls CUDA-13 wheels (inferred uninstallable on Apple Silicon) and supply-chain integrity is unverifiable (unsafe-best-match can shadow intended packages). — M
- **[P1] Not reproducible-from-lockfile for the deployed daemon** — CI uses `uv sync --locked`, but deploy installs a release-time `requirements.txt` with no hashes; the running set isn't verifiable against `uv.lock`. — M
- **[P1] Version-source drift** — `.release-please-manifest.json` = 0.6.1 while live is 0.7.0; a release was deployed outside the release-please flow. — S
- **[P1] Install depends on a bare `uv` on PATH** of whatever context launches the daemon (headless service account without `uv` → raw FileNotFoundError). — S
- **[P2] No `daemon doctor`/GC** to detect/remove superseded venvs or stale launchers; upgrades accrete dead trees (the 3-venv state). — S
- **[P2] macOS untested in CI** (Ubuntu+Windows only). — S
- **[P2] Personal absolute path leaks into published docstrings** (`hooks/tools/*_injector.py`: `C:\Users\jeffr\.pepper\...`). — S

---

## Theme C — Multi-tenancy & hatchery  *(dimensions 4, 7)*

- **[P0] Single monolithic daemon/config/port hosts all beings** — one `~/.agent-core/agent_core.yaml` + one bus + one HTTP host; fragments "may not override bus/http". No per-tenant process/config/failure isolation; a bad endpoint entry or boot error drops every being. Co-tenancy, not multi-tenancy. — L
- **[P0] Scheduled-task drift** (also Theme 0): the production autostart is a hand-authored `AgentCoreDaemon` outside version control and outside the tool's install/uninstall/refresh path; `daemon install` operates on a task nobody uses. — M
- **[P1] The hatchery never generates `.mcp.json`.** `hatcher.py:120-150` renders only `agent_core.yaml` + `.claude/settings.json` + `CLAUDE.md`; the one file that wires a being to the bus is a hand-copied doc snippet — the most error-prone step, left to the human. — S
- **[P1] Mixed config mechanisms per being.** `endpoints.d/` has only `wren.yaml`; pepper's endpoints live inline in the monolith. The isolation primitive (conf.d fragments) exists in code but isn't the uniform reality. — M
- **[P1] No config schema validation for the daemon monolith** — `runner.py:47-228` parses via raw `dict.get()` + ad-hoc `BusBootError`; the `validate_config` hookspec is an explicit no-op. A typo'd param crashes at boot (all tenants), not at write time. — M
- **[P1] Hatchery writes secrets plaintext** — Discord token → `discord-<being>.env` `TOKEN=<literal>` (chmod 0600 is a silent no-op on Windows, the live platform); no keyring/vault integration despite the `creds-management` skill existing. — M
- **[P2] No hatch→run handoff** — `daemon_check_status = "skipped"`; fragments are written but the daemon isn't reloaded/verified, so a "successful" hatch isn't live until a manual bounce. — S
- **[P2] Config-drift debris** — 5+ dated `.bak`/`.pre-*` monolith copies; manual "mv aside" editing; install stamp doesn't record the venv path in use; no drift detection. — M
- **[P2] Hatchery reuses none of core's config schemas** — generator and consumer can drift; shared schema would make generation correct-by-construction. — M

*(Strengths to preserve: `HatchConfig` pydantic contract with `extra="forbid"`, `StrictUndefined` rendering, transactional rollback, file-class coverage audit, conf.d merge primitive.)*

---

## Theme D — Security hardening  *(dimension 5)*

- **[P0] Vault master password persisted plaintext, world-readable by default.** `credentials/cli.py:62-63` writes `AGENT_CORE_VAULT_PASSWORD=` to `~/.agent-core/.env` via plain `open("a")` — no `chmod 0600`. The KeePass vault's whole security reduces to a file next to the data it unlocks. Move to OS keyring/DPAPI or at minimum enforce owner-only perms and don't co-locate. — S
- **[P0] Secrets printed to stdout + inherited by every subprocess.** `creds get --json` emits `{"password": ...}` (`cli.py:103-114`); the vault password + webhook secret live in `os.environ` and are inherited by every forked child (`/proc/<pid>/environ`, crash dumps). Needs non-printing default + scrubbed `env=` allowlist per subprocess. — M
- **[P1] Bus MCP surface has zero authentication.** `runner.py:120 has_auth_hook=False` (hardcoded); loopback bind is the *only* control. Any local process can `POST http://127.0.0.1:8788/mcp/<being>/` and send/ack/read that being's mail with full authority (identity is path-based). Needs per-endpoint bearer token / unix-socket peer-cred even on loopback. *(Top P1 — mitigated by loopback, but below the framework bar.)* — M
- **[P1] Audit logs are plain append-only JSONL, not tamper-evident** (`mcp_audit/writer.py`, `inbound/audit.py` — bare `open("a")`, no hash-chain/HMAC/seq). Post-hoc tampering is undetectable. — M
- **[P1] Inbound webhook has no replay window.** `funnel_handler.py:92-101` verifies only HMAC (signatures don't expire); dedup is in-memory LRU (4096) that resets on restart, and rate-limited denials deliberately skip dedup — a captured valid delivery replays after a restart or 4096 events. Bind a freshness signal / persist delivery-ids. — M
- **[P2] Notification body projection is a prompt-injection surface** — attacker-controlled `issue.title`/`pr.title`/`commit.message` flow verbatim (only length-capped) into the being's LLM context (`github_connector.py:81-83`). Mark external text as untrusted/delimited at the framework boundary. — M
- **[P2] Unbounded webhook body read before HMAC** (`funnel_handler.py:48 await request.body()` — memory pressure vector); cap size first. — S
- **[P2] Rate limiter is one global `(source,target)` bucket** — a signed flood starves legitimate high-priority events; no burst/priority carve-out. — M
- **[P2] `.env` loader injects arbitrary keys** into the process env with no allowlist (`cli.py:27-35`) — a tampered `.env` can set `PATH`/`PYTHONPATH`. — S

*(Strengths to preserve: constant-time HMAC with `sha256=` guard, server-stamped sender identity, structural-only audit summaries (no secret values), hard-refused non-loopback bind, `docs_url=None`.)*

---

## Theme E — Observability & data durability  *(dimensions 6, 9)*

- **[P0] No backup/restore for either SQLite store.** No `.backup`/`VACUUM INTO` anywhere; `bus.sqlite` (all in-flight mail) + `scheduler.db` (every schedule) are un-backed-up single files. Disk failure/corruption = total loss with no recovery. — M
- **[P1] No built-in health/degraded-state detection.** `daemon status` reports only PID-liveness; the bus learns an endpoint is dead only when it tries to `deliver()`. A silently-dead endpoint queues mail while status says "running" — which is exactly why resilience is outsourced to the external scheduler ping. Needs `/healthz` + per-endpoint last-success/heartbeat. — M
- **[P1] No structured logging.** `logging.basicConfig` plain text only; envelope/correlation ids are formatted into strings, not queryable fields. No JSON handler in core. — M
- **[P1] Daemon log is a single unbounded file, no rotation** (`daemon/cli.py:155`); grows until the disk fills. — S
- **[P1] No WAL checkpointing or VACUUM.** WAL is enabled but never checkpointed/truncated; the hot path "never deletes" so acked/expired rows accumulate forever; only DLQ is manually purgeable. Unbounded growth. — M
- **[P1] No retention/compaction for terminal envelopes** (acked/expired retained forever). — M
- **[P2] No corruption detection/recovery** — no `PRAGMA integrity_check`/quarantine; a malformed DB crashes daemon boot. — M
- **[P2] Ad-hoc single-column migration, no version tracking** — one hand-rolled `PRAGMA table_info` ALTER; no `user_version`/migration table. — M
- **[P2] Scheduler store not on WAL** — `scheduler.db` runs default rollback-journal (weaker crash-safety than the bus), and it drives every heartbeat/liveness fire. — S
- **[P2] Health/metrics require shelling in** — no `/healthz`/`/metrics` route; `bus status`/`daemon status` are CLI-only and each boot a fresh bus + second store connection. — M
- **[P2] Audit/raw JSONL trails have no retention/rotation-by-size** — daily files accumulate forever. — S

*(Strengths to preserve: WAL + in-flight-requeue + TTL/DLQ sweeps + 0600 perms + hot-path indices; secret-safe async MCP audit; opt-in `bus_tail` debug surface with `tail`/`trace_correlation`/`metrics` tools.)*

---

## Theme F — Quality, testing & documentation  *(dimensions 8, 10, 11)*

**Testing/CI & tech-debt**
- **[P1] The release-gate never runs in CI.** `agent-core-qa` (the 7-scenario dynamic-install/round-trip validator) isn't referenced by any workflow and isn't in `testpaths`; it runs only if a human points it at a hand-started daemon. A release can ship without the running-daemon path validated. — M
- **[P1] Real-subprocess/real-sleep tests aren't `slow`-marked** and run in the default parallel lane (`test_daemon_cli.py:71`, `test_daemon_supervisor.py:57`) — the flakiness + process-leak surface (root cause of the 4 stranded servers: `_kill()` terminates only the direct child, not the tree; the framework's own `kill_tree` helper is unused here). — S
- **[P1] `agent-core-notify` ships with zero tests and no mypy** (not in `testpaths`, not in `[tool.mypy] files`). — M
- **[P2] mypy covers only 2 of 12 packages** (`core` + `channel`); the 2033-line discord endpoint is entirely untyped. — M
- **[P2] Two god-modules** — discord `endpoint.py` (2033 lines) and `claude_code_mcp.py` (1137 lines, "not started" guard repeated 7×). — L
- **[P2] `audit.py` copy-pasted 4 ways** (briefs/voice/webcam/inbound near-identical) — hoist a generic `JsonlAuditLog` + shared tool-invocation endpoint base into `core`. — M
- **[P2] Minor: unlogged `except: pass` swallows (×4), a no-op skipped stub test, untested email-send path.** — S

**Docs / onboarding**
- **[P0] No end-to-end getting-started** — README is 19 lines; no path from clone → hatch → run → connect for a new person. — L
- **[P0] Docs assume the reader is Wren** — daemon.md is "the live Pepper+Wren home", inbound README hardcodes `~/.wren`/`wrenrichley`/foreman; no install that isn't Jeff's private GitHub-Release feed. — L
- **[P0] No architecture overview** — the bus+daemon+sidecar+pluggable-endpoint model lives only in ~50 dated spec files + a stale ROADMAP; never stated in one place. — M
- **[P1] No "hatch your own being" walkthrough**, no CONTRIBUTING, most packages (incl. `core` and `busproxy`) have no README, no general "add an endpoint" reference, bus config keys undocumented outside a sample. — M
- **[P1] `CLAUDE.md` structure map is stale** — describes a top-level `src/agent_core/` that doesn't exist (real layout is `packages/*/src/`, core at `packages/core/src/agent_core/`). — S
- **[P2] ROADMAP stale** (2026-04-30; marks shipped subsystems "Not started"). — S

*(Strengths to preserve: `docs/setup/daemon.md` is a world-class *operator* runbook; releases/ci docs solid; `docs/extensions.md` cleanly documents the envelope-kind plugin seam; inbound README is an excellent (if Wren-specific) endpoint runbook.)*

---

## Suggested sequencing (for the design session to confirm)

1. **Theme 0 today** — defuse landmines (pure risk, no design).
2. **Themes A + B together** — resilience/supervision and portable lifecycle are the same rework (a portable supervisor that owns the daemon lifecycle, resolves its own env, and self-heals cross-platform) and directly close #260 + the 2026-07-09 class. This is the natural first design epic.
3. **Theme C** — multi-tenancy/hatchery, which depends on B's env/launch decisions (per-being isolation, `.mcp.json` generation).
4. **Theme D** — security hardening (partly independent; the two P0 secret items could ride with Theme 0/B).
5. **Theme E** — observability + durability (health/backup/retention).
6. **Theme F** — quality/docs, ongoing, with the adopter-docs P0s gated on the architecture settling from A–C.

**Net:** the core earns its keep; the work is to lift supervision, lifecycle, isolation, secrets, operability, and onboarding to the same standard — all natively, no Docker, so no adopter ever needs it.
