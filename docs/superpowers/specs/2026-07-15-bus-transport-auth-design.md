# Bus transport authentication — Design (Theme D, Cluster β)

**Theme:** agent_core#267 (Theme D — Security hardening) · epic #262
**Date:** 2026-07-15
**Status:** approved design, pre-implementation
**Priority:** `[P1]` cluster (bus auth). Not auto-planned — held for explicit go.
**Cluster:** Dβ of Theme D. Sibling clusters: Dα secret-material handling (spec `2026-07-15-security-secret-handling-design.md`, tickets #345–348), Dγ inbound integrity & abuse resistance, Dδ untrusted-input boundary (later brainstorms).

## Problem

The bus has **no caller authentication**. Identity is entirely positional: a request to `/mcp/<being>/` is *assumed* to come from `<being>` because that is the path it hit. The only thing standing between an arbitrary local process and any being's inbox is the loopback bind.

- **`has_auth_hook = False` is hardcoded** (`bus/runner.py:147`). The v1 invariant (`_validate_http`, `runner.py:39-45`) refuses any non-loopback bind *because* there is no auth — loopback is the entire security boundary. That permanently pins the bus to a single host: it can never bind a LAN/Tailscale interface to let beings on other machines talk to it, because doing so would expose every being's inbox to that network unauthenticated. `[P1]`
- **Any local process can impersonate any being.** On a shared host (and our hosts *are* shared — beings run under the same OS user), any process that can reach `127.0.0.1:8789` can `POST /mcp/pepper/` and speak as Pepper: send envelopes as her, drain her queue, ack her messages. Nothing verifies the caller *is* the being named in the path. `[P1]`
- **Peer-credential (SO_PEERCRED / getpeereid) does not disambiguate beings.** The obvious "just check the OS uid of the connecting socket" fails here precisely because Wren and Pepper share the same OS user — peer-cred proves *which user*, never *which being*. Identity has to be carried by something each being holds and no sibling does. `[P1]`

## Design decisions (from the brainstorm, approved)

1. **Asymmetric per-being identity: Ed25519 keypair + signed-JWT bearer; the bus verifies with the public key.** Each being holds a private Ed25519 key in its own vault. Its outbound MCP client (busproxy) mints a short-lived JWT, signs it with that private key (`EdDSA`), and sends it as `Authorization: Bearer <jwt>`. The bus verifies the signature against the being's *public* key. We deliberately chose asymmetric over a symmetric shared bearer token: **the bus ends up holding no secrets at all — only public keys, which are safe to sit in plaintext config — and each being's private key never leaves its vault.** A leaked bus config cannot impersonate anyone. This is standard public-key auth (the JWT/JWK shape), not a bespoke scheme.

2. **Identity is bound to the path.** The JWT's `sub` (and `iss`) claim names the being. The bus verifies two things together: (a) the signature is valid under the public key registered for the being in the URL path, and (b) the claimed being in the token *equals* the being in the path. This closes the "valid token for being A replayed against being B's path" hole — a token only works at its own being's mount.

3. **Three-mode enforcement flag, `warn → enforce` rollout — no being loses access.** `bus_auth_mode: off | warn | enforce`.
   - `off` — today's behavior, no check (default until migration completes).
   - `warn` — verify where a bearer is present, but still accept unauthenticated requests, logging each as unauthenticated. This is the migration window: nothing breaks, and the logs show exactly who has not cut over yet.
   - `enforce` — missing/invalid/mismatched signature → **401, request rejected**.
   Rollout: provision keypairs (hatchery for new beings, migration for Wren/Pepper) → set `warn` → confirm from logs that every being authenticates cleanly → flip to `enforce`. New beings are born keypair-ready, so they are enforce-ready from birth. This mirrors the Dα principle: security hardening must not cut Pepper or Wren off mid-flight.

4. **Failure behavior in `enforce`: hard 401, transparent re-mint on expiry.** A missing, malformed, expired, wrong-signature, or path-mismatched token → `401`, surfaced by busproxy as a clear auth error (not a silent drop) so the being knows to check its key. Tokens are short-lived; busproxy re-mints and re-signs from the private key on demand, so expiry is invisible to the being — that is the point of using short-lived tokens instead of a long-lived static credential.

5. **Simple rotation; dual-key window deferred (YAGNI).** A `rotate` operation regenerates a being's keypair, writes the new private key to its vault and the new public key to its endpoint config. v1 accepts a brief re-auth during a maintenance beat; we do **not** build a dual-key acceptance window (bus trusting old+new public key simultaneously) unless rotation frequency ever demands zero-downtime rotation. Add it later if needed.

6. **Hatchery provisions the keypair at hatch.** New-being creation generates the Ed25519 keypair as part of hatch: the private key is written to the being's vault, the public key to the bus-side endpoint config (`endpoints.d/<being>.yaml` or equivalent). Public keys are not secret, so writing them into config committed/synced alongside other endpoint metadata is fine. This is the mechanism that makes decision 3's "born keypair-ready" true.

## Architecture

### 1. Keypair + JWT primitives (`security/` or `credentials/`)

- Ed25519 keygen, private-key load (from vault, via the Dα secret accessor), public-key load (from endpoint config). Sign a JWT (`alg: EdDSA`) with claims `iss`/`sub` = being name, `iat`, `exp` (short TTL), `aud` = bus. Verify a JWT against a supplied public key, returning the validated claims or a typed failure.
- No new heavyweight dependency if avoidable: `PyJWT[crypto]` (already pulls `cryptography`) covers EdDSA sign/verify, or `cryptography` directly. Chosen library pinned in the plan.
- Pure, side-effect-free, unit-testable in isolation — this is the crypto core the other pieces consume.

### 2. Bus-side verification middleware (`bus/http_host.py`, `bus/runner.py`)

- An ASGI middleware wrapping the top-level router (the existing `_app` shim in `http_host.py` is where the path is already inspected — the natural insertion point). For each request under a `/mcp/<being>/` mount:
  1. Read `bus_auth_mode`. If `off`, pass through.
  2. Extract `Authorization: Bearer`. In `warn`, absent bearer → log-unauthenticated + pass through; present → verify and log the result. In `enforce`, absent → 401.
  3. Load the public key registered for `<being>` (from endpoint config). Verify signature + `exp` + that token `sub` == path being. On any failure in `enforce` → 401; in `warn` → log + pass through.
- `has_auth_hook` (`runner.py:147`) flips from a hardcoded `False` to `bus_auth_mode != "off"`, so `_validate_http` will permit a non-loopback bind **only** once auth is actually enforced — the loopback invariant and the auth mode become a single coupled decision.
- Public-key registry: a small loader that maps being → public key from the endpoint config, refreshed on config reload (same lifecycle as other endpoint metadata).

### 3. Busproxy / outbound signing (being-side MCP client)

- The outbound client that talks to the bus mints a short-lived JWT signed with the being's private key (fetched via the Dα vault accessor — never from `os.environ`) and attaches `Authorization: Bearer`. It caches the token until near `exp`, then transparently re-mints. A `401` response triggers an immediate re-mint-and-retry once (covers clock skew / rotation).

### 4. Hatchery provisioning + rotation (`hatchery/`, CLI)

- Hatch generates the keypair, writes private → new being's vault, public → bus endpoint config for that being. Idempotent: re-hatch / repair does not clobber an existing keypair unless `--rotate` is passed.
- `rotate <being>`: regenerate, write new private to vault, new public to config; the next mint uses the new key. No dual-key window (decision 5).
- Migration for existing beings (Wren, Pepper): a one-time provision that generates keypairs for beings that predate auth and installs them, coordinated with Pepper for her vault. Runs under `warn` until each being is observed authenticating cleanly, then the operator flips `enforce`.

## Ticket decomposition (dependency-ordered)

- **Dβ-1 — Ed25519 keypair + EdDSA-JWT sign/verify primitives.** *(no dep)* The crypto core: keygen, sign short-lived JWT, verify against a public key with claim validation. Pure + unit-tested.
- **Dβ-2 — Bus verification middleware + `bus_auth_mode` (off|warn|enforce) + path-identity binding + `has_auth_hook` wiring.** *(blocked_by Dβ-1)* Removes the "any local process impersonates any being" P1; makes non-loopback bind conditional on enforced auth.
- **Dβ-3 — Busproxy outbound signing: mint/cache/refresh bearer from vault private key + 401 re-mint-retry.** *(blocked_by Dβ-1)* The being-side half; depends on the primitives, not on the middleware.
- **Dβ-4 — Hatchery keypair provisioning at hatch + `rotate` command.** *(blocked_by Dβ-1)* Makes new beings born keypair-ready; owns the key lifecycle.
- **Dβ-5 — Migration for existing beings (Wren/Pepper) + `warn`→`enforce` cutover.** *(blocked_by Dβ-2, Dβ-3, Dβ-4)* The end-to-end enablement: provision the pre-auth beings, run in `warn`, verify, flip `enforce`. Coordinated with Pepper.

## Testing / validation

- **Primitives:** round-trip sign→verify passes; tampered payload fails; expired `exp` fails; wrong public key fails; `sub`≠expected fails. No secret (private key) ever appears in captured output.
- **Middleware:** table-driven over the three modes. `off` passes everything. `warn` passes unauthenticated but logs it, and verifies a present token. `enforce` returns 401 on absent/malformed/expired/wrong-sig/path-mismatch and 200 on a valid token. Explicit test: a valid token for being A → 401 at being B's path.
- **Busproxy:** signs with the private key from the (Dα) vault accessor, not env; re-mints before expiry; on a 401 re-mints and retries exactly once. A test asserts the private key is never logged.
- **Hatchery:** hatch produces a vault private key + a config public key; the pair round-trips through the primitives; re-hatch is idempotent; `rotate` invalidates the old key (old token → 401 under `enforce`).
- **Migration:** a being with no keypair works under `warn` (logged unauthenticated), then authenticates after provisioning; flipping `enforce` before provisioning would 401 it (asserted as the guard rationale).
- **Cross-platform:** keypair storage rides the Dα vault/keyring path, so its per-OS behavior is covered there; the crypto (`cryptography`/`PyJWT`) is platform-independent — no OS-gated tests expected here.

## Dependencies & sequencing

- **Depends on Dα** for the private-key-at-rest path: the being's Ed25519 private key is a secret and must live in the vault behind the Dα secret accessor (decision Dα-3), never in `os.environ`. Dβ-3 and Dβ-4 consume the Dα accessor. Dβ-1 (pure crypto) can start in parallel with Dα; Dβ-3/4 land after Dα-1/Dα-3.
- **Hatchery coupling:** Dβ-4 extends the hatch flow — it must land compatibly with the hatchery correctness work (`2026-07-14-hatchery-correctness-design.md`) rather than forking it.

## Strengths to preserve

The loopback-only invariant and its fail-loud refusal (`_validate_http`) are the current security floor — Dβ does not weaken them; it makes them *conditional on enforced auth* so the floor can be safely raised to a network bind later. Server-stamped sender identity (Dα strength) composes with this: the bus stamps *who sent it* and now also *proves they are who they claim*. `docs_url=None`, constant-time HMAC, and structural-only audit summaries are untouched.
