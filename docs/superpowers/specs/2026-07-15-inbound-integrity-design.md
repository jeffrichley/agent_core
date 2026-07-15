# Inbound integrity & abuse resistance — Design (Theme D, Cluster γ)

**Theme:** agent_core#267 (Theme D — Security hardening) · epic #262
**Date:** 2026-07-15
**Status:** approved design, pre-implementation
**Priority:** `[P1]` cluster. Not auto-planned — held for explicit go.
**Cluster:** Dγ of Theme D. Siblings: Dα secret-material handling (spec `2026-07-15-security-secret-handling-design.md`, #345–348), Dβ bus transport auth (spec `2026-07-15-bus-transport-auth-design.md`, #352–356), Dδ untrusted-input boundary (later brainstorm).

## Problem

The inbound webhook path (the one public ingress, exposed via Tailscale Funnel) is **already well-defended at the authenticity layer** — and this cluster deliberately preserves that:

- HMAC-SHA256 signature verification with a constant-time compare and a `sha256=` guard (`funnel_handler.py:92-101`); bad/missing → 401.
- Cross-redelivery de-dupe (`router.py:122-124`), keyed on `(connector, event_id, target_being)`.
- Per-`(source, target)` token-bucket rate limiting (`endpoint.py`, default 30/min).
- Signed-but-malformed JSON → 400 (not 500), so GitHub stops retrying; fail-loud on a missing secret at construction.

Dγ closes the **four residual gaps** around that core — availability and generalization, not authenticity:

1. **Unauthenticated body-read DoS (sharpest).** `funnel_handler.py:48` does `raw = await request.body()` — it reads the *entire* body into memory **before** the signature check, with no size limit. Anyone who can reach the Funnel URL (no secret) can POST a multi-GB body → memory exhaustion + CPU burned hashing it. The rate limiter sits *after* signature verification, so it does not protect this path. `[P1, highest severity in Dγ]`
2. **Replay is capacity-bounded, not time-bounded.** The de-dupe cache is a fixed-size LRU. A validly-signed delivery replayed *after* its key evicts passes again — GitHub signs the body, not a timestamp, so there is no freshness gate. `[P1]`
3. **Integrity is GitHub-hardcoded.** The HMAC check lives inline in the GitHub handler. Any *future* inbound connector (a generic webhook, an email push) has no shared "verify-before-receive" contract to inherit — a new connector could be wired in with no integrity check at all. `[P1]`
4. **No payload-shape limit.** Attacker-controlled JSON is parsed (and walked via dotted paths by the connector matcher) with no nesting-depth guard — a deeply-nested JSON structure is a cheap CPU sink even behind a valid signature. `[P1]`

Boundary vs **Dδ**: Dγ answers *"is this request authentic, fresh, and non-abusive at the transport/protocol layer?"* Dδ answers *"the content is authentic, but its meaning is untrusted"* (prompt-injection into a being's context). Dγ stops when the router is handed a verified, fresh, size- and shape-bounded event.

## Design decisions (from the brainstorm, approved)

1. **Body-size cap enforced *before* the body is read — per-endpoint configurable, default 1 MiB.** Reject on `Content-Length` over the cap before `await request.body()`, plus a streamed-read guard that aborts once the cap is exceeded (defends a lying or absent `Content-Length`). The cap is a **per-endpoint config value** (alongside the existing `rate_limit_per_minute`), not a global constant: different connectors have different payload profiles — a connector that legitimately receives images sets a higher cap without weakening the default for webhook connectors. Default **1 MiB** (real GitHub webhooks are <100 KB; GitHub's own hard max is 25 MB).

2. **De-dupe becomes time-bounded (age-based eviction) — configurable, default 24 h TTL.** The dedupe cache evicts entries by **age**, not only LRU capacity, so a key cannot silently evict-then-replay. Default **24 h** comfortably covers GitHub's redelivery-retry window (redeliveries reuse the same delivery GUID), so every legitimate redelivery is still caught as a duplicate while the cache holds only delivery-IDs (tiny). Capacity bounding stays as a secondary guard against unbounded growth; age is the primary, security-relevant eviction.

3. **Connector-agnostic `verify()` contract — no default-allow.** Add `verify(headers, raw_body) -> bool` to the Connector protocol. The ingress calls `connector.verify(...)` generically *before* `router.receive(...)`; the GitHub connector's implementation is the existing HMAC check, moved out of the funnel handler. A connector with no `verify` implementation is **not** allowed by default — the contract fails closed, so a future connector cannot be added without consciously providing (or explicitly opting out of) integrity.

4. **JSON nesting-depth cap — configurable, default 64.** After the size cap and before/at parse, bound the maximum JSON nesting depth to kill deeply-nested-JSON CPU abuse. Default **64** — far beyond any legitimate webhook (which are a few levels deep), shallow enough to reject a nesting bomb. Configurable per-endpoint for symmetry with the size cap.

All three numeric limits are **configurable with the stated defaults** — per Jeff, the body-size cap in particular must be operator-tunable for image/large-payload connectors.

## Architecture

### 1. Body-size cap (`funnel_handler.py`, `endpoint.py`)

- `endpoint.py` gains a `max_body_bytes: int = 1 << 20` param (default 1 MiB), threaded through to `build_funnel_app` alongside the existing secret/rate-limit wiring.
- The handler checks `Content-Length` before reading; if present and over the cap → `413 Payload Too Large`. It then reads the body with a cap-bounded guard (accumulate chunks, abort at the cap) so an absent/lying `Content-Length` cannot smuggle an oversized body. Rejection happens **before** signature verification and before full-body materialization.

### 2. Time-bounded de-dupe (`router.py`)

- The `_seen` LRU gains age-based eviction: each entry records an insertion time; lookups and inserts evict entries older than `dedupe_ttl_seconds` (default 86 400). Capacity bounding remains as a backstop. The dedupe key is unchanged.
- Because the router has no wall clock injected today, a monotonic/`time`-source seam is added for testability (inject a clock, as the scheduler does), so the TTL is unit-testable without real sleeps.

### 3. Connector-agnostic `verify()` (`protocol.py`/connector, `funnel_handler.py`, `github_connector.py`)

- Extend the Connector protocol with `verify(headers: Mapping[str, str], raw_body: bytes) -> bool`. The GitHub connector implements it with the current `_verify_signature` HMAC (moved from the funnel handler). The generic ingress calls `connector.verify(...)`; on `False` → 401, before `router.receive(...)`.
- Fail-closed: the protocol default (or the ingress) treats a missing/None verify as reject, not allow. Existing behavior is preserved for GitHub (same HMAC, same 401), just relocated behind the contract.

### 4. JSON nesting-depth cap (`funnel_handler.py`)

- `endpoint.py` gains `max_json_depth: int = 64`. The handler enforces the depth bound when parsing the (already size-capped) body — either a depth-limited parse or a post-parse depth walk — returning `400` on violation (a malformed/abusive body won't be fixed by redelivery). Composes after the size cap so the depth walk only ever runs on a bounded body.

## Ticket decomposition (dependency-ordered)

- **Dγ-1 — Body-size cap before read (per-endpoint config, default 1 MiB).** *(no dep)* Closes the unauthenticated body-read DoS — the highest-severity gap. Touches `funnel_handler.py` + `endpoint.py`.
- **Dγ-2 — Time-bounded de-dupe (age-based eviction, default 24 h TTL) + clock seam.** *(no dep — isolated to `router.py`)* Closes the evict-then-replay hole.
- **Dγ-3 — Connector-agnostic `verify()` contract (relocate GitHub HMAC, fail-closed).** *(blocked_by Dγ-1 — shares `funnel_handler.py`; lands after the size cap)* Generalizes integrity so no future connector is added unverified.
- **Dγ-4 — JSON nesting-depth cap (per-endpoint config, default 64).** *(blocked_by Dγ-3 — shares `funnel_handler.py`; lands after the verify refactor)* Closes the nested-JSON CPU sink; composes on top of the size cap.

Dγ-1 and Dγ-2 can proceed in parallel (different files). Dγ-3 and Dγ-4 serialize behind Dγ-1 to keep the `funnel_handler.py` edits conflict-free.

## Testing / validation

- **Body-size:** a request with `Content-Length` over the cap → 413 before any body read; a request that lies about (or omits) `Content-Length` and streams an oversized body → aborted at the cap, not fully buffered; a normal <cap webhook still processes; the per-endpoint override raises the effective cap.
- **De-dupe TTL:** an entry within the TTL is de-duped (second delivery dropped); an entry past the TTL is *not* de-duped (re-processed) — asserted via the injected clock, no real sleep; capacity backstop still trims. Explicit: a redelivery at T+23h is a dupe, at T+25h is fresh.
- **verify() contract:** GitHub path behaves exactly as before (valid HMAC → through, bad/missing → 401), now via `connector.verify`; a connector with no verify implementation is rejected (fail-closed), asserted with a fake connector.
- **JSON depth:** a payload nested past the cap → 400; a normal shallow payload → through; the depth check runs only on a size-bounded body.
- **Regression:** the existing HMAC, de-dupe, rate-limit, and malformed-JSON→400 behaviors are unchanged (their tests still pass unmodified except where the HMAC moves behind `verify`).

## Strengths to preserve

Constant-time HMAC with the `sha256=` guard, the 401-on-bad-signature contract, malformed-JSON→400 (GitHub stops retrying), fail-loud-on-missing-secret at construction, `docs_url=None`/`redoc_url=None`, and the per-`(source,target)` rate limiter. Dγ relocates the HMAC behind a contract and adds bounds *in front of* it — it weakens none of these.
