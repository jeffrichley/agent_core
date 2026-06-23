"""Test-only speedup: reuse the vault's derived key to skip repeated KDF runs.

A KeePass ``.kdbx`` vault derives its encryption key from the master password
with Argon2 — a deliberately slow, memory-hard KDF whose whole purpose is to
make brute-forcing the master password expensive. ``CredentialStore`` opens the
vault once per operation (get/set/list/delete each call ``_open``), so a single
test that does a few CRUD calls pays the Argon2 cost several times over (~0.3s
each), and the credentials suite was ~15-20s of pure key-stretching.

pykeepass 4.1.1 offers no way to lower the stored KDF parameters after creation
(the parsed header isn't re-serialized from the params dict — mutating it
desyncs the key from the header). The supported lever is ``transformed_key``:
pass the already-derived key to ``create_database`` / ``PyKeePass`` / ``save``
and the KDF is skipped entirely.

This autouse fixture captures the transformed key the first time each vault file
is created (the real Argon2 derivation still runs **once**, so the genuine
password->key crypto path is exercised), then reuses that key for every later
open and save of the same file. Saves must reuse the key too, because saving
rewrites the header salt; the returned object's ``save`` is wrapped to thread it
through. Net effect: ~0.6s per test (one real derivation) instead of ~1.5-2.5s,
with no loss of crypto fidelity and no production-code changes.
"""

from __future__ import annotations

import pytest

from agent_core_credentials import store as _store


@pytest.fixture(autouse=True)
def fast_vault_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    real_create = _store.create_database
    real_open = _store.PyKeePass
    keys: dict[str, bytes] = {}

    def _wrap_save(kp: object, key: bytes) -> None:
        original = kp.save  # type: ignore[attr-defined]

        def save(filename: object = None, transformed_key: bytes | None = None) -> object:
            return original(filename=filename, transformed_key=transformed_key or key)

        kp.save = save  # type: ignore[attr-defined]

    def fast_create(filename: object, password: str | None = None, **kw: object) -> object:
        kp = real_create(filename, password=password, **kw)
        keys[str(filename)] = kp.transformed_key
        _wrap_save(kp, keys[str(filename)])
        return kp

    def fast_open(filename: object, password: str | None = None, **kw: object) -> object:
        key = keys.get(str(filename))
        if key is not None:
            kp = real_open(filename, transformed_key=key, **kw)
        else:
            # First touch of a pre-existing vault: run the real KDF once, cache it.
            kp = real_open(filename, password=password, **kw)
            keys[str(filename)] = kp.transformed_key
        _wrap_save(kp, keys[str(filename)])
        return kp

    monkeypatch.setattr(_store, "create_database", fast_create)
    monkeypatch.setattr(_store, "PyKeePass", fast_open)
