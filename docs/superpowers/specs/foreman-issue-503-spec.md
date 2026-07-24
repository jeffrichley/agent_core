# Spec: public-key registry loader (being → pubkey) (issue #503)

## Goal

Implement `PubkeyRegistry`, a small immutable value object that maps being names to their registered Ed25519 public keys, loaded from a new `pubkey_pem` field on each `EndpointEntryConfig`. The registry is built fresh on every `build_bus_from_config` call (giving it config-reload semantics at zero extra cost), returns a typed miss (`None`) for unregistered beings, and is threaded into `HTTPHost` so the bus auth middleware (Dβ-2b) can access it without touching the `build_bus_from_config` return signature. This is Dβ-2a under the bus transport auth design (`docs/superpowers/specs/2026-07-15-bus-transport-auth-design.md`, issue #353).

## Acceptance criteria

- `EndpointEntryConfig` in `packages/core/src/agent_core/bus/config.py` has a `pubkey_pem: str | None = None` field. Existing YAML without `pubkey_pem` continues to validate correctly.
- `PubkeyRegistry.lookup(being: str) -> Ed25519PublicKey | None` returns the parsed key for a configured being and `None` for an unregistered one. The `None` return is the typed miss.
- `build_pubkey_registry(entries: list[EndpointEntryConfig]) -> PubkeyRegistry` parses each entry's `pubkey_pem`, logs an ERROR and skips entries whose PEM is malformed or is not an Ed25519 key, and returns a registry containing all valid entries.
- A second `build_pubkey_registry` call with updated entries (simulating config reload) produces a new, independent registry that reflects the updated pubkeys.
- `cryptography>=42.0` is listed as a direct dependency in `packages/core/pyproject.toml`.
- `HTTPHost.__init__` accepts an optional `pubkey_registry: PubkeyRegistry | None = None` keyword argument and stores it as `self._pubkey_registry`. Callers that do not pass it (all existing tests) are unaffected.
- `build_bus_from_config` builds a `PubkeyRegistry` from `daemon_cfg.endpoints` and passes it to `HTTPHost`.
- Unit tests in `packages/core/tests/bus/test_pubkey_registry.py` cover: empty entries → empty registry; configured being → correct `Ed25519PublicKey`; unregistered being → `None`; invalid PEM → entry skipped, error logged, other entries still resolved; wrong key type (non-Ed25519) → entry skipped, error logged; config-reload scenario → new registry reflects updated key.
- `just check` exits 0 on the resulting branch.

## Approach

No GoF pattern applies. This is a straightforward immutable value object following SRP: `PubkeyRegistry`'s single responsibility is "resolve being name → public key." The name "registry" fits common auth vocabulary (JWK registry) without introducing a heavyweight abstraction.

**No in-process hot-reload needed.** The phrase "refreshed on config reload" means the registry is rebuilt whenever `build_bus_from_config` is called. The daemon already tears down and reinitializes the entire `Bus`/`HTTPHost` stack on config reload, so building a new `PubkeyRegistry` inside `build_bus_from_config` gives refresh semantics for free — no observable state survives between calls.

**Schema placement: first-class field on `EndpointEntryConfig`, not in `params`.** The `params` dict is forwarded to endpoint class constructors (`runner.py:204`: `cls(name=entry.name, **constructor_params)`). Putting `pubkey_pem` in `params` would be injected into every endpoint class that doesn't expect it — causing failures unless it were added to `reserved_params`. Adding it as a first-class optional field on `EndpointEntryConfig` keeps it at the config schema level, where `extra="forbid"` catches typos (`pub_key_pem`), and cleanly separates auth metadata from endpoint constructor arguments. The `None` default is backward-compatible.

**`cryptography` as a direct dependency.** The `cryptography` package is the correct library for Ed25519 public key parsing (`load_pem_public_key` + `isinstance(raw, Ed25519PublicKey)`). It is not currently listed in `packages/core/pyproject.toml` as a direct dependency (it may be transitively available via `fastmcp` or `pykeepass`, but direct listing is required for correctness). `cryptography>=42.0` ships `py.typed` so mypy resolves it without `ignore_missing_imports` overrides.

**Error handling at parse time: skip and log.** Following the "graceful degradation" pattern established by `_EntryBusBootError` in `runner.py`, a malformed `pubkey_pem` does not crash boot. It is logged at ERROR level and the being is absent from the registry. Under `bus_auth_mode=warn` (Dβ-2b, not this ticket) an absent being is treated as unauthenticated. Under `enforce` it gets 401. For the current `off` default, the registry sits unused.

**`HTTPHost` as the registry anchor.** The `HTTPHost` owns the ASGI router and is the natural insertion point for the bus auth middleware (Dβ-2b, `http_host.py`). Attaching the registry via an optional constructor parameter is backward-compatible (all callers that omit it get `None`), keeps `build_bus_from_config`'s return signature unchanged, and avoids coupling `Bus` to auth concerns.

## Sub-requests (topologically sorted)

1. **Add `cryptography` to `packages/core/pyproject.toml`.** In the `[project] dependencies` list, add:
   ```
   "cryptography>=42.0",
   ```
   Place it after `uvicorn>=0.30`.

2. **Add `pubkey_pem` field to `EndpointEntryConfig` in `packages/core/src/agent_core/bus/config.py`.**
   Change `EndpointEntryConfig` from:
   ```python
   class EndpointEntryConfig(BaseModel):
       model_config = ConfigDict(extra="forbid")
       type: str
       name: str
       params: dict[str, Any] = Field(default_factory=dict)
       description: str = ""
   ```
   to:
   ```python
   class EndpointEntryConfig(BaseModel):
       model_config = ConfigDict(extra="forbid")
       type: str
       name: str
       params: dict[str, Any] = Field(default_factory=dict)
       description: str = ""
       pubkey_pem: str | None = None
   ```
   No other changes to `config.py`.

3. **Create `packages/core/src/agent_core/bus/auth/__init__.py`.** New empty package marker:
   ```python
   """Bus transport authentication sub-package (Theme D Cluster β)."""
   ```

4. **Create `packages/core/src/agent_core/bus/auth/pubkey_registry.py`.** Full implementation:
   ```python
   """Public-key registry: being → Ed25519PublicKey, loaded from endpoint config.

   Dβ-2a in the bus transport auth design
   (docs/superpowers/specs/2026-07-15-bus-transport-auth-design.md).
   """

   from __future__ import annotations

   import logging

   from cryptography.exceptions import UnsupportedAlgorithm
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
   from cryptography.hazmat.primitives.serialization import load_pem_public_key

   from agent_core.bus.config import EndpointEntryConfig

   log = logging.getLogger(__name__)


   class PubkeyRegistry:
       """Immutable mapping from being name to its registered Ed25519 public key.

       Built from endpoint config at daemon boot / config reload.
       Refresh by calling build_pubkey_registry with the new DaemonConfig entries.
       """

       def __init__(self, keys: dict[str, Ed25519PublicKey]) -> None:
           """Construct from a pre-validated mapping. Not for direct use — call
           build_pubkey_registry() instead."""
           self._keys: dict[str, Ed25519PublicKey] = keys

       def lookup(self, being: str) -> Ed25519PublicKey | None:
           """Return the public key for *being*, or None if not registered."""
           return self._keys.get(being)

       def __len__(self) -> int:
           return len(self._keys)

       def __repr__(self) -> str:
           return f"PubkeyRegistry({sorted(self._keys.keys())!r})"


   def build_pubkey_registry(entries: list[EndpointEntryConfig]) -> PubkeyRegistry:
       """Load the pubkey_pem from each endpoint entry into an immutable registry.

       Entries without pubkey_pem are silently skipped.
       Entries with an unparseable or non-Ed25519 PEM are skipped after logging
       an ERROR; the remaining entries are still loaded.

       Args:
           entries: the EndpointEntryConfig list from DaemonConfig.

       Returns:
           A fresh PubkeyRegistry mapping being name → Ed25519PublicKey.
       """
       keys: dict[str, Ed25519PublicKey] = {}
       for entry in entries:
           if entry.pubkey_pem is None:
               continue
           try:
               raw_key = load_pem_public_key(entry.pubkey_pem.encode())
           except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
               log.error(
                   "endpoint %r: pubkey_pem is not a valid PEM public key — "
                   "being absent from pubkey registry: %s",
                   entry.name,
                   exc,
               )
               continue
           if not isinstance(raw_key, Ed25519PublicKey):
               log.error(
                   "endpoint %r: pubkey_pem is a %s key, expected Ed25519 — "
                   "being absent from pubkey registry",
                   entry.name,
                   type(raw_key).__name__,
               )
               continue
           keys[entry.name] = raw_key
       return PubkeyRegistry(keys)
   ```

5. **Update `HTTPHost.__init__` in `packages/core/src/agent_core/bus/http_host.py`.**
   Add import at the top of the file (after existing imports):
   ```python
   from agent_core.bus.auth.pubkey_registry import PubkeyRegistry
   ```
   Extend `HTTPHost.__init__` with an optional parameter:
   ```python
   def __init__(
       self,
       *,
       bind_host: str = "127.0.0.1",
       bind_port: int = 8788,
       notify_broker: NotificationBroker | None = None,
       notify_snapshot: Callable[[str], dict | None] | None = None,
       pubkey_registry: PubkeyRegistry | None = None,
   ):
       self._bind_host = bind_host
       self._requested_port = bind_port
       self._mounts: list[MCPHostable] = []
       self._server: uvicorn.Server | None = None
       self._serve_task: asyncio.Task | None = None
       self._started = False
       self._notify_broker = notify_broker
       self._notify_snapshot = notify_snapshot
       self._pubkey_registry = pubkey_registry   # consumed by auth middleware (Dβ-2b)
   ```
   No other changes to `HTTPHost` in this ticket.

6. **Update `build_bus_from_config` in `packages/core/src/agent_core/bus/runner.py`.**
   Add import at the top of the file alongside the other `agent_core.bus` imports:
   ```python
   from agent_core.bus.auth.pubkey_registry import build_pubkey_registry
   ```
   After `daemon_cfg = DaemonConfig.model_validate(raw)` (currently line 92), add:
   ```python
   pubkey_registry = build_pubkey_registry(daemon_cfg.endpoints)
   ```
   Update the `HTTPHost(...)` construction (currently near line 272) to pass the registry:
   ```python
   http_host = HTTPHost(
       bind_host=daemon_cfg.http.bind_host,
       bind_port=daemon_cfg.http.bind_port,
       notify_broker=notify_broker,
       notify_snapshot=bus.snapshot_for_agent,
       pubkey_registry=pubkey_registry,
   )
   ```

7. **Write `packages/core/tests/bus/test_pubkey_registry.py`.** Full test module:
   ```python
   """Tests for PubkeyRegistry and build_pubkey_registry (Dβ-2a)."""

   from __future__ import annotations

   import logging

   import pytest
   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
   from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

   from agent_core.bus.auth.pubkey_registry import PubkeyRegistry, build_pubkey_registry
   from agent_core.bus.config import EndpointEntryConfig


   def _make_entry(name: str, pubkey_pem: str | None = None) -> EndpointEntryConfig:
       return EndpointEntryConfig.model_validate(
           {"type": "builtin.stub", "name": name, "pubkey_pem": pubkey_pem}
       )


   def _generate_pubkey_pem() -> str:
       private = Ed25519PrivateKey.generate()
       return (
           private.public_key()
           .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
           .decode()
       )


   class TestPubkeyRegistryLookup:
       def test_empty_registry_returns_none_for_any_being(self):
           registry = build_pubkey_registry([])
           assert registry.lookup("pepper") is None

       def test_configured_being_resolves_to_key(self):
           pem = _generate_pubkey_pem()
           registry = build_pubkey_registry([_make_entry("pepper", pem)])
           key = registry.lookup("pepper")
           assert key is not None

       def test_unregistered_being_returns_none(self):
           pem = _generate_pubkey_pem()
           registry = build_pubkey_registry([_make_entry("pepper", pem)])
           assert registry.lookup("wren") is None

       def test_multiple_beings_resolved_independently(self):
           pem_pepper = _generate_pubkey_pem()
           pem_wren = _generate_pubkey_pem()
           registry = build_pubkey_registry([
               _make_entry("pepper", pem_pepper),
               _make_entry("wren", pem_wren),
           ])
           assert registry.lookup("pepper") is not None
           assert registry.lookup("wren") is not None
           assert registry.lookup("pepper") != registry.lookup("wren")

       def test_entry_without_pubkey_pem_excluded(self):
           registry = build_pubkey_registry([_make_entry("pepper", None)])
           assert registry.lookup("pepper") is None

       def test_len_counts_valid_entries_only(self):
           pem = _generate_pubkey_pem()
           registry = build_pubkey_registry([
               _make_entry("pepper", pem),
               _make_entry("wren", None),
           ])
           assert len(registry) == 1


   class TestPubkeyRegistryConfigReload:
       def test_new_registry_reflects_updated_key(self):
           pem_v1 = _generate_pubkey_pem()
           pem_v2 = _generate_pubkey_pem()
           registry_v1 = build_pubkey_registry([_make_entry("pepper", pem_v1)])
           registry_v2 = build_pubkey_registry([_make_entry("pepper", pem_v2)])
           key_v1 = registry_v1.lookup("pepper")
           key_v2 = registry_v2.lookup("pepper")
           assert key_v1 is not None
           assert key_v2 is not None
           # Different keypairs → different key objects
           assert key_v1 != key_v2

       def test_registry_v1_unchanged_after_v2_built(self):
           pem_v1 = _generate_pubkey_pem()
           pem_v2 = _generate_pubkey_pem()
           registry_v1 = build_pubkey_registry([_make_entry("pepper", pem_v1)])
           build_pubkey_registry([_make_entry("pepper", pem_v2)])  # v2, discard
           # v1 registry is unaffected by building v2
           assert registry_v1.lookup("pepper") is not None


   class TestPubkeyRegistryErrorHandling:
       def test_malformed_pem_skipped_with_error_logged(self, caplog):
           with caplog.at_level(logging.ERROR, logger="agent_core.bus.auth.pubkey_registry"):
               registry = build_pubkey_registry([_make_entry("pepper", "not-valid-pem")])
           assert registry.lookup("pepper") is None
           assert any("pepper" in r.message and "pubkey_pem" in r.message for r in caplog.records)

       def test_bad_entry_does_not_prevent_good_entry(self, caplog):
           pem = _generate_pubkey_pem()
           with caplog.at_level(logging.ERROR, logger="agent_core.bus.auth.pubkey_registry"):
               registry = build_pubkey_registry([
                   _make_entry("pepper", "not-valid-pem"),
                   _make_entry("wren", pem),
               ])
           assert registry.lookup("pepper") is None
           assert registry.lookup("wren") is not None

       def test_wrong_key_type_skipped_with_error_logged(self, caplog):
           # Generate an RSA public key PEM to trigger the "not Ed25519" branch.
           from cryptography.hazmat.primitives.asymmetric import rsa
           from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
           rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
           rsa_pem = rsa_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
           with caplog.at_level(logging.ERROR, logger="agent_core.bus.auth.pubkey_registry"):
               registry = build_pubkey_registry([_make_entry("pepper", rsa_pem)])
           assert registry.lookup("pepper") is None
           assert any("Ed25519" in r.message for r in caplog.records)
   ```
   
   Mark the RSA-keygen test as slow (RSA key generation exceeds the small-test cap):
   ```python
       @pytest.mark.slow  # rsa.generate_private_key with key_size=2048 is heavy crypto
       def test_wrong_key_type_skipped_with_error_logged(self, caplog):
           ...
   ```

8. **Update `packages/core/tests/bus/test_config.py` to cover the new field.**
   Add to `TestEndpointEntryConfig`:
   ```python
   def test_pubkey_pem_defaults_to_none(self):
       entry = EndpointEntryConfig.model_validate({"type": "builtin.stub", "name": "ep"})
       assert entry.pubkey_pem is None

   def test_pubkey_pem_accepts_string(self):
       entry = EndpointEntryConfig.model_validate(
           {"type": "builtin.stub", "name": "ep", "pubkey_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"}
       )
       assert entry.pubkey_pem is not None

   def test_extra_field_still_rejected_with_pubkey_pem_present(self):
       with pytest.raises(pydantic.ValidationError):
           EndpointEntryConfig.model_validate(
               {"type": "builtin.stub", "name": "ep", "pubkey_pem": "x", "unknown_extra": "oops"}
           )
   ```

9. **Verify the gate.**
   ```bash
   just check
   ```
   Expected: green (lint, mypy, tests, coverage, patch-cov all pass). The new `test_pubkey_registry.py` slow-marked RSA test skips in the fast lane; it runs under the slow-tests CI job.

## File-level changes

| File | Action | What changes |
|---|---|---|
| `packages/core/pyproject.toml` | Modify | Add `"cryptography>=42.0"` to `[project] dependencies` |
| `packages/core/src/agent_core/bus/config.py` | Modify | Add `pubkey_pem: str | None = None` field to `EndpointEntryConfig` |
| `packages/core/src/agent_core/bus/auth/__init__.py` | Create | New package marker (1-line docstring) |
| `packages/core/src/agent_core/bus/auth/pubkey_registry.py` | Create | `PubkeyRegistry` class + `build_pubkey_registry()` function |
| `packages/core/src/agent_core/bus/http_host.py` | Modify | Add `pubkey_registry: PubkeyRegistry | None = None` to `HTTPHost.__init__`; store as `self._pubkey_registry` |
| `packages/core/src/agent_core/bus/runner.py` | Modify | Import and call `build_pubkey_registry`; pass result to `HTTPHost` constructor |
| `packages/core/tests/bus/test_pubkey_registry.py` | Create | Unit tests for `PubkeyRegistry` and `build_pubkey_registry` |
| `packages/core/tests/bus/test_config.py` | Modify | Add 3 tests for `pubkey_pem` field on `EndpointEntryConfig` |

No changes to test conftest, justfile, CI workflows, or any other packages. No endpoint class constructors change — `pubkey_pem` is not in `params`.

## Alternatives considered

1. **Store `pubkey_pem` in `EndpointEntryConfig.params` instead of as a first-class field.** `params` keys are forwarded to endpoint class constructors; `pubkey_pem` would need to be added to `reserved_params` to avoid being injected into classes that don't expect it. A first-class field is schema-explicit (`extra="forbid"` catches typos), cleanly separated from constructor params, and doesn't require modifying the plugin reserved-params contract. Ruled out.

2. **Return the registry from `build_bus_from_config` as a third tuple element.** Would make the registry directly accessible to all callers without modifying `HTTPHost`. However, all existing call sites use `bus, http = await build_bus_from_config(...)` tuple unpacking — adding a third element breaks every caller. Attaching to `HTTPHost` (where the middleware will live) is the correct extension point with zero breakage. Ruled out.

3. **Build the registry lazily (only when auth mode != "off").** The registry construction is O(N endpoints) with one PEM parse per entry — negligible overhead at boot. Lazy construction adds complexity and a thread-safety surface that would need attention when `bus_auth_mode` becomes a config field (Dβ-2b). Eager construction is simpler, consistent with how other config-derived objects are built at boot. Ruled out.

## Open questions

1. **Location of Dβ-1 primitives.** The issue states "Depends on Dβ-1 primitives, already shipped." This spec found no `agent_core.bus.auth.primitives` (or equivalent) module in the codebase. If Dβ-1 is available in a different location and already wraps `load_pem_public_key`, the Worker should prefer calling the Dβ-1 wrapper over calling `cryptography` directly, for consistency with the rest of the auth cluster. If Dβ-1 is not yet accessible from `packages/core`, the `cryptography` direct call is correct.

2. **`cryptography` transitive availability.** `cryptography>=42.0` may already be transitively reachable (via `pykeepass` / `agent-core-credentials` or `fastmcp`). Adding the direct dependency is still required (transitive-only availability is fragile) and is the correct action regardless.

## Out of scope

- `bus_auth_mode: off | warn | enforce` config field — that is Dβ-2b.
- The ASGI auth middleware in `http_host.py` — Dβ-2b.
- `has_auth_hook` rewiring in `runner.py` (`runner.py:165` comment) — Dβ-2b.
- Busproxy outbound signing (`packages/agent-core-busproxy/`) — Dβ-3.
- Hatchery keypair provisioning at hatch — Dβ-4.
- Migration for existing beings (Wren/Pepper) — Dβ-5.
- Any change to the endpoint class constructors or the `reserved_params` contract.
- Mypy strict override for `agent_core.bus.auth.*` — not required by this ticket; the existing lighter mypy baseline for `packages/core/src` applies.
