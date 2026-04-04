"""Tests for sagewai.fleet.encryption — FleetPayloadEncryption."""

from __future__ import annotations

import pytest

from sagewai.fleet.encryption import FleetPayloadEncryption


class TestFleetPayloadEncryption:
    """Unit tests for FleetPayloadEncryption."""

    def test_roundtrip(self) -> None:
        """encrypt then decrypt returns original payload."""
        key = FleetPayloadEncryption.generate_key()
        enc = FleetPayloadEncryption(org_keys={"org1": key})

        payload = '{"task": "build report", "priority": 1}'
        ciphertext = enc.encrypt("org1", payload)
        assert ciphertext != payload  # actually encrypted
        assert enc.decrypt("org1", ciphertext) == payload

    def test_passthrough_no_keys(self) -> None:
        """Without any keys, encrypt/decrypt are identity functions."""
        enc = FleetPayloadEncryption()

        payload = '{"hello": "world"}'
        assert enc.encrypt("org1", payload) == payload
        assert enc.decrypt("org1", payload) == payload

    def test_passthrough_unknown_org(self) -> None:
        """Unknown org_id returns payload unchanged."""
        key = FleetPayloadEncryption.generate_key()
        enc = FleetPayloadEncryption(org_keys={"org1": key})

        payload = '{"secret": true}'
        assert enc.encrypt("org_unknown", payload) == payload
        assert enc.decrypt("org_unknown", payload) == payload

    def test_different_org_keys(self) -> None:
        """Different orgs produce different ciphertext for same payload."""
        key1 = FleetPayloadEncryption.generate_key()
        key2 = FleetPayloadEncryption.generate_key()
        enc = FleetPayloadEncryption(org_keys={"org1": key1, "org2": key2})

        payload = "same payload"
        ct1 = enc.encrypt("org1", payload)
        ct2 = enc.encrypt("org2", payload)
        assert ct1 != ct2  # different keys => different ciphertext

    def test_decrypt_wrong_key_raises(self) -> None:
        """Decrypting with the wrong org key raises an error."""
        key1 = FleetPayloadEncryption.generate_key()
        key2 = FleetPayloadEncryption.generate_key()
        enc1 = FleetPayloadEncryption(org_keys={"org1": key1})
        enc2 = FleetPayloadEncryption(org_keys={"org1": key2})

        ciphertext = enc1.encrypt("org1", "secret data")
        with pytest.raises(Exception):  # InvalidToken from cryptography
            enc2.decrypt("org1", ciphertext)

    def test_generate_key_valid(self) -> None:
        """generate_key produces a valid Fernet key (44-char base64)."""
        key = FleetPayloadEncryption.generate_key()
        assert isinstance(key, str)
        assert len(key) == 44  # Fernet keys are 44 base64 chars

        # Key should work when used to init a new instance
        enc = FleetPayloadEncryption(org_keys={"test": key})
        ct = enc.encrypt("test", "hello")
        assert enc.decrypt("test", ct) == "hello"

    def test_has_key(self) -> None:
        """has_key returns True only for configured orgs."""
        key = FleetPayloadEncryption.generate_key()
        enc = FleetPayloadEncryption(org_keys={"org1": key})

        assert enc.has_key("org1") is True
        assert enc.has_key("org2") is False

    def test_has_key_no_keys(self) -> None:
        """has_key returns False when no keys are configured."""
        enc = FleetPayloadEncryption()
        assert enc.has_key("anything") is False

    def test_empty_payload(self) -> None:
        """Encrypting an empty string works correctly."""
        key = FleetPayloadEncryption.generate_key()
        enc = FleetPayloadEncryption(org_keys={"org1": key})

        ct = enc.encrypt("org1", "")
        assert enc.decrypt("org1", ct) == ""

    def test_unicode_payload(self) -> None:
        """Unicode payloads survive roundtrip."""
        key = FleetPayloadEncryption.generate_key()
        enc = FleetPayloadEncryption(org_keys={"org1": key})

        payload = '{"name": "Hausverwaltung", "lang": "de"}'
        ct = enc.encrypt("org1", payload)
        assert enc.decrypt("org1", ct) == payload

    def test_none_org_keys(self) -> None:
        """Passing None explicitly is the same as no keys."""
        enc = FleetPayloadEncryption(org_keys=None)
        assert enc.encrypt("x", "data") == "data"
        assert enc.has_key("x") is False
