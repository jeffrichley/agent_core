"""Tests for PubkeyRegistry and build_pubkey_registry (Dβ-2a)."""

from __future__ import annotations

import logging

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from agent_core.bus.auth.pubkey_registry import build_pubkey_registry
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

    @pytest.mark.slow  # rsa.generate_private_key with key_size=2048 is heavy crypto
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
