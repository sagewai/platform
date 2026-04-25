# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for Sealed-i Fernet wrapping."""
import pytest
from cryptography.fernet import Fernet

from sagewai.sealed.crypto import Crypto, SecretCorrupted


def test_encrypt_decrypt_round_trip():
    key = Fernet.generate_key()
    c = Crypto(key)
    ct = c.encrypt("hello world")
    assert ct.startswith("fernet:")
    assert c.decrypt(ct) == "hello world"


def test_decrypt_rejects_missing_prefix():
    key = Fernet.generate_key()
    c = Crypto(key)
    with pytest.raises(SecretCorrupted, match="missing fernet prefix"):
        c.decrypt("not-fernet-format")


def test_decrypt_rejects_wrong_key():
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    c1 = Crypto(key1)
    c2 = Crypto(key2)
    ct = c1.encrypt("hello")
    with pytest.raises(SecretCorrupted, match="Fernet decrypt failed"):
        c2.decrypt(ct)


def test_multifernet_old_key_decryption_works():
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    old_only = Crypto(key1)
    rotation_phase = Crypto(key2, previous_keys=[key1])
    ct_old = old_only.encrypt("hello")
    assert rotation_phase.decrypt(ct_old) == "hello"


def test_rotate_value_re_encrypts_with_primary():
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    rot = Crypto(key2, previous_keys=[key1])
    old = Crypto(key1)
    ct_old = old.encrypt("hello")
    ct_new = rot.rotate_value(ct_old)
    # New ciphertext decrypts under new-only too:
    new_only = Crypto(key2)
    assert new_only.decrypt(ct_new) == "hello"
    # Old key alone can't decrypt the new ciphertext:
    with pytest.raises(SecretCorrupted):
        old.decrypt(ct_new)
