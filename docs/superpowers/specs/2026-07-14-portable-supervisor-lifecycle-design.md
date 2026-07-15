# Portable bus-daemon supervisor & lifecycle — Design (Theme B, Cluster 3)

**Theme:** agent_core#265 (Theme B — portable install & lifecycle) · epic #262
**Date:** 2026-07-14
**Status:** approved design, pre-implementation
**Also lands:** Theme A #264 line-item #4's OS-level restart half (folded into Theme B 2026-07-14).

## Problem

The bus daemon's lifecycle is Windows-only and fragile:
- Autostart is **Windows-Task-Scheduler-only** and **hard-exits on non-win32** (`daemon/cli.py:408-410`) — no launchd/systemd.
- On Windows it runs as a **closeable console window under `InteractiveToken`** (`autostart.py:46`) — a window-close or logoff kills the whole bus, taking every being down. This happened **twice on 2026-07-13**.
- There is **no detection of a hung-but-alive daemon** — a wedged event loop with a live process is invisible (the 2026-07-14 foreman crash-loop class, generalized): the process exists, so nothing restarts it.

The daemon is a **single shared bus** (one instance serves all beings; beings attach client-side via the `busproxy` MCP sidecar from their vaults) — NOT one daemon per being.

## Design decisions (from the brainstorm, approved)

1. **Single, user-level bus service.** One bus (not per-being), running as the primary user so it natively sees `~/.agent-core` + the being vaults + keyring. System-level rejected: a root/service-account daemon wouldn't see the user's home/vaults/keyring without re-creating user-scoping.
2. **OS-native service manager per OS** owns process lifecycle (boot/login start, keep-alive, headless). No custom cross-platform supervisor process — the OS does supervision. Headless comes for free (services have no console window → kills the 7/13 death class), and native restart policies give Theme A's OS-level restart with zero custom code.
3. **Two supervision layers, cleanly separated:**
   - **In-process (Theme A #272/#273):** `EndpointSupervisor` quarantines/restarts individual endpoints; the daemon stays up degraded through partial failure.
   - **OS process-restart (this theme):** the OS service manager restarts the whole process only on death.
4. **Portable liveness watchdog** for the hung-but-alive gap: the main loop bumps a heartbeat; an off-loop thread `os._exit`s if the loop wedges → the OS restarts a fresh process. Protects all three OSes including Windows.
5. **Windows = true service (not scheduled task).** A scheduled task's restart is count-bounded, which the watchdog's self-terminate loop would exhaust; a true service with recovery "restart always" is unbounded.

## Architecture

### Liveness watchdog (in-daemon)

- The daemon's core loops (dispatch / sweep tick) update a monotonic `last_progress` timestamp.
- A dedicated **OS thread** (deliberately NOT an asyncio task — a wedged event loop must not also wedge the watcher) wakes on a timer and compares `now - last_progress` against `watchdog_timeout_seconds`.
- If exceeded, it logs a CRITICAL structured `WatchdogFired` event and calls `os._exit(EX_UNRECOVERABLE)` — the OS service manager restarts a fresh process.
- **systemd integration:** when running under systemd with `WatchdogSec` set, the same heartbeat also calls `sd_notify(WATCHDOG=1)` so the native watchdog participates (belt + suspenders on Linux).
- Config: `watchdog_timeout_seconds` (default e.g. 90; `> 0` guard; a non-positive value disables — mirrors existing watchdog-guard convention), env-overridable per the config-provenance chain.
- The heartbeat freshness is also exposed as a health signal (a heartbeat file / `bus status` field) that Theme E and an optional tray icon can consume.

### Per-OS service unit + `install-autostart`

`agent-core daemon install-autostart` becomes **cross-platform**: detect the OS, generate + register the right unit, idempotently; `uninstall-autostart` removes it; re-register on upgrade. Replaces the current win32-only hard-exit.

- **Linux** — systemd **`--user`** unit at `~/.config/systemd/user/agent-core.service`: `Restart=always`, `WatchdogSec` (pairs with sd_notify), `RestartSec` backoff. Install runs `systemctl --user enable --now` and prints the `loginctl enable-linger <user>` step (survive logout / start at boot).
- **macOS** — launchd **LaunchAgent** plist at `~/Library/LaunchAgents/<label>.plist`: `KeepAlive=true`, `RunAtLoad=true`. Install runs `launchctl bootstrap gui/<uid>`.
- **Windows** — a **true Windows Service** (headless, no console) with **failure recovery = "restart the service, 0-min delay, always"** (unbounded). Runs as the user (service logon = the user account, so it sees the vault/keyring). Replaces the `InteractiveToken` console task.

### Invocation

The unit invokes a stable **`agent-core-daemon`** console-script entry point (added to `[project.scripts]`) on the stable `~/.agent-core/.venv` — no version-stamped interpreter path in the unit. (The being-side `.mcp.json` version-stamped paths are a separate Cluster 2 item, not in scope here.)

### Composition with Theme A

The daemon stays up degraded (Theme A EndpointSupervisor) and only exits — for the OS to restart — on: an unrecoverable crash, or the watchdog firing on a wedged loop. The OS service's always-restart is the backstop; Theme A is the first line.

## Ticket decomposition (3 tickets)

- **B-1 · Portable liveness watchdog** *(no dependency; do first)*
  Heartbeat timestamp on the core loops + off-loop watcher thread + `os._exit` on wedge + `sd_notify(WATCHDOG=1)` when applicable + `watchdog_timeout_seconds` config + `WatchdogFired` structured event + health-signal exposure. Platform-agnostic, unit-testable standalone (fake clock + a deliberately-wedged loop). Protects even the current Windows setup partially and fully once B-3 lands.

- **B-2 · Cross-platform autostart framework + Linux/macOS** *(no dependency)*
  Refactor `install-autostart`/`uninstall-autostart` into an OS-dispatch that no longer hard-exits off-win32; implement the systemd `--user` unit generator + install (`systemctl --user enable --now`, surface `enable-linger`) and the launchd LaunchAgent plist generator + install (`launchctl bootstrap`). Add the `agent-core-daemon` console-script entry point. Native `Restart=always`/`KeepAlive`. Keep the existing Windows path working (untouched) until B-3.

- **B-3 · Windows headless service** *(blocked_by B-2 — needs the OS-dispatch framework)*
  Replace the `InteractiveToken` scheduled task with a true Windows Service (service wrapper — WinSW/pywin32; decide in impl), headless, running as the user, recovery = restart-always-unbounded. This makes the watchdog's self-terminate→restart reliable on the live platform and removes the closeable-console death for good.

## Non-goals (deferred / out of scope)

- **Tray icon** — an optional observability/control surface on top of the running service (status indicator, menu). Its own later ticket; the health signal (heartbeat + `bus status` degraded list) is the interface it will consume.
- **Cluster 1 (distribution/cu130/PyPI)** and **Cluster 2 (interpreter shim, being-side `.mcp.json`, daemon doctor/GC, `uv`-on-PATH)** — separate Theme B sub-epics.
- **Mechanical items** (`__version__` sync, macOS CI, test path-leak cleanup) — separate, foreman-able independently.
- No custom cross-platform supervisor process; no system-level service.

## Testing

- **Watchdog (B-1):** with a fake clock, a loop that stops bumping the heartbeat triggers exactly one `WatchdogFired` + the exit hook (injected, not a real `os._exit`, in tests); a healthy loop never fires; a non-positive timeout disables it; the watcher runs off the event loop (a blocked loop still lets the watcher fire).
- **Autostart (B-2):** unit/plist generators emit the expected content for given params (golden-file); `install-autostart` dispatches to the right generator per `sys.platform` and no longer hard-exits off-win32; idempotent re-install; `uninstall` removes cleanly. (OS registration calls mocked in CI; a manual/integration check per OS documented.)
- **Windows service (B-3):** service-config generation asserts headless + recovery=always; install/uninstall idempotent (mocked SCM in CI; manual Windows verification documented). Regression: the console window no longer appears.
