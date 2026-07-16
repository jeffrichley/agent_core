# Observability — Design (Theme E, Cluster α)

**Theme:** agent_core#268 (Theme E — Observability & data durability) · epic #262
**Date:** 2026-07-15
**Status:** approved design, pre-implementation
**Priority:** P1/P2 cluster (eval 2026-07-13, Theme E, dimension 6). No P0 items; not auto-planned — held for explicit go.
**Cluster:** Eα of Theme E — the observability half (sibling: **Eβ** data durability, spec `2026-07-15-data-durability-design.md`, #373–378). Theme E was split because *seeing* the running system and *surviving* data loss are independent subsystems.

## Problem

Health is **PID-only** and logs are **plain-text and unbounded**:

- **`[P1]` No health/degraded-state detection.** `daemon status` reports only PID-liveness (`daemon/cli.py` — `is_alive`/`read_pid` + "last 20 lines of daemon.log"). The bus learns an endpoint is dead only when it tries to `deliver()`. A **silently-dead endpoint queues mail while status says "running"** — which is exactly why liveness is currently outsourced to an external scheduler ping.
- **`[P1]` No structured logging.** Plain `logging` text only; envelope/correlation ids are formatted into strings, not queryable fields. No JSON handler in core.
- **`[P1]` Daemon log is a single unbounded file, no rotation** — grows until the disk fills (a live risk, per the 2026-07-15 C:-full incident).
- **`[P2]` Health/metrics require shelling in** — no `/healthz`/`/metrics` route; `bus status`/`daemon status` are CLI-only and each boot a fresh bus + second store connection.

There is already a shared HTTP host (`bus/http_host.py` — a Starlette `Router` with per-endpoint `Mount`s and a `_notify` `Route` on :8789) and a supervisor state layer (`supervisor_state` / quarantine from the supervision work) — the seams the observability layer builds on.

## Design decisions (from the brainstorm, approved)

1. **Composite per-endpoint health model.** An endpoint is **healthy** iff: (a) it has **heartbeat within a window** (it stamps a liveness timestamp on its own loop — catches the silently-dead-idle endpoint that has no mail to fail on), AND (b) it is **not quarantined** by the supervisor (catches the crashed/quarantined case — reuses the existing `supervisor_state`), AND (c) its **last delivery outcome was not a failure** (catches the live-but-erroring case). A bus-level health registry aggregates the three signals. Chosen over heartbeat-only or last-success-only because each of those misses a real failure mode; the composite catches all three and reuses supervision infra rather than a parallel mechanism.

2. **`/healthz` + `/metrics` on the shared HTTP host (no shelling in).** Mount two routes on the existing http_host (:8789):
   - `/healthz` → **200** if all endpoints healthy, **503** if any degraded, with a **JSON** body giving the per-endpoint breakdown (state + which of the three signals is failing). Machine- and human-readable; a bespoke live-status UI can consume it directly.
   - `/metrics` → **Prometheus text exposition format** (the industry-standard scrapeable format — Grafana/Prometheus-ready with zero custom charting). Counters/gauges: queue depth per endpoint, DLQ depth, per-endpoint delivery success/fail totals, heartbeat age. Emitted by hand or via `prometheus_client`; no JSON variant (agent_core metrics feed agent_core's own ops view — a foreman dashboard is a *separate* effort in the foreman repo, out of scope here).

3. **Structured (JSON) logging with queryable correlation ids.** A JSON log formatter in core (stdlib `logging` + a JSON formatter — no new dependency required). Envelope/correlation ids become **structured fields**, not string-interpolated text, via a `contextvar` set at the start of an envelope's handling and propagated through it. A config toggle selects JSON (prod) vs pretty (dev) so human iteration stays readable.

4. **Daemon-log rotation.** Replace the single unbounded file handler with a `RotatingFileHandler`. **Default 10 MB × 5 files** (≈50 MB ceiling), **operator-configurable** (size + backup count), consistent with the other configurable caps in this epic.

## Architecture

### 1. Health registry (`bus/`, e.g. `health.py`)

- A `HealthRegistry` holding per-endpoint records: `last_heartbeat`, `last_outcome` (ok/fail + timestamp), and a read of supervisor quarantine state. `heartbeat(endpoint)` is called by each endpoint's loop; `record_outcome(endpoint, ok)` is called on each `deliver()`. `status()` computes per-endpoint health = heartbeat-fresh AND not-quarantined AND last-outcome-ok, and an overall roll-up.
- Heartbeat-freshness window is config-driven (default a small multiple of the endpoint's expected cadence). The registry has no wall clock of its own — inject a clock (as the scheduler/dedupe do) so it is unit-testable without sleeps.

### 2. HTTP routes (`bus/http_host.py`)

- Add `Route("/healthz", ...)` and `Route("/metrics", ...)` to the shared Router alongside the existing `_notify` route. `/healthz` serializes `HealthRegistry.status()` to JSON with the right status code (200/503). `/metrics` renders the registry + bus counters in Prometheus text.
- No new port; both live on the existing :8789 host.

### 3. Structured logging (`core` logging setup)

- A JSON formatter + a `contextvar` (`correlation_id`) bound when envelope handling begins, so every log line emitted during that handling carries the id as a field. A config key selects the JSON handler (prod) or the human-readable one (dev). Existing log call sites keep working; the ids stop being string-baked.

### 4. Log rotation (`daemon/cli.py` logging setup)

- Swap the daemon-log file handler for `logging.handlers.RotatingFileHandler(maxBytes=<cfg>, backupCount=<cfg>)`, defaults 10 MB / 5, both read from config.

## Ticket decomposition (dependency-ordered)

- **Eα-1 — Composite per-endpoint health registry (heartbeat + quarantine + last-outcome) + clock seam.** *(no dep)* `[P1]` — closes the silently-dead-endpoint gap. Held.
- **Eα-2 — `/healthz` (JSON, 200/503) + `/metrics` (Prometheus text) routes on the shared HTTP host.** *(blocked_by Eα-1 — needs the registry)* `[P2]` — removes the shell-in requirement. Held.
- **Eα-3 — Structured JSON logging + correlation-id contextvar + JSON/pretty config toggle.** *(no dep)* `[P1]`. Held.
- **Eα-4 — Daemon-log rotation (RotatingFileHandler, configurable, default 10 MB × 5).** *(no dep)* `[P1]`. Held.

Eα-1, Eα-3, Eα-4 are independent (parallelizable); Eα-2 serializes behind Eα-1.

## Testing / validation

- **Health registry:** a fresh-heartbeat + not-quarantined + last-ok endpoint reads healthy; each of a stale heartbeat, a quarantined state, and a last-failure independently flips it to degraded; the overall roll-up is degraded iff any endpoint is; all asserted via the injected clock (no real sleeps).
- **`/healthz`:** 200 + JSON breakdown when all healthy; 503 when any degraded; the JSON names the failing signal per endpoint.
- **`/metrics`:** returns valid Prometheus text (`# TYPE`/`# HELP` + `name{labels} value` lines) parseable by a Prometheus scraper; the expected counters/gauges are present with correct labels.
- **Structured logging:** with the JSON handler, a log emitted during envelope handling is valid JSON carrying the `correlation_id` field; the toggle selects pretty output in dev; no correlation id bleeds across unrelated handlings (contextvar isolation).
- **Rotation:** the daemon log rotates at the configured size, keeps the configured backup count, and never exceeds the ceiling; the config overrides the defaults.

## Strengths to preserve

The opt-in `bus_tail` debug surface (`tail`/`trace_correlation`/`metrics` tools) and the secret-safe async MCP audit stay as-is; Eα adds always-on health/metrics/structured-logs *alongside* them without changing delivery semantics. `docs_url=None`/`redoc_url=None` on the HTTP host is preserved — `/healthz` and `/metrics` are explicit routes, not doc surfaces.
