# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-project encryption keys (W5).

Envelope encryption: each project owns a random Fernet *data key*, wrapped
under the org *master key* and persisted in ``project.data_key_ref``. A leaked
project key cannot decrypt another project's secrets — the crypto boundary
matches the tenancy boundary. Org-shared secrets (``project_id is None``) are
encrypted under the org master key directly.

Runs on SQLite always, and on Postgres when SAGEWAI_TEST_DATABASE_URL is set
(via the dual-dialect ``dialect_engine`` fixture in tests/db/conftest.py).
"""

import asyncio

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from sagewai.admin.identity_store import IdentityStore, TenantAccessError
from sagewai.admin.tenant_keys import (
    decrypt_for_project,
    encrypt_for_project,
    project_crypto,
    rewrap_project_data_key,
)
from sagewai.sealed.crypto import Crypto, SecretCorrupted


@pytest_asyncio.fixture
async def store(dialect_engine):
    s = IdentityStore(engine=dialect_engine)
    await s.init()
    return s


@pytest.fixture
def master_key() -> bytes:
    """A fresh org master key, injected so tests never touch real key custody."""
    return Fernet.generate_key()


async def _org_with_projects(store, *slugs: str) -> tuple[str, list[str]]:
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pids = [(await store.create_project(oid, slug, slug.upper()))["id"] for slug in slugs]
    return oid, pids


async def test_round_trip_project_secret(store, master_key):
    oid, (pa,) = await _org_with_projects(store, "alpha")
    ct = await encrypt_for_project(store, oid, pa, "super-secret", master_key=master_key)
    assert ct.startswith(Crypto.PREFIX)
    assert "super-secret" not in ct
    assert await decrypt_for_project(store, oid, pa, ct, master_key=master_key) == "super-secret"


async def test_data_key_wrapped_under_master_and_persisted(store, master_key):
    oid, (pa,) = await _org_with_projects(store, "alpha")
    await encrypt_for_project(store, oid, pa, "x", master_key=master_key)

    wrapped = await store.get_project_data_key(oid, pa)
    assert wrapped is not None
    assert wrapped.startswith(Crypto.PREFIX)
    # The stored blob is the data key encrypted under the master key — the org
    # master Crypto unwraps it to a valid 44-char Fernet key (never plaintext).
    data_key = Crypto(master_key).decrypt(wrapped)
    assert len(data_key) == 44
    # The key is stable: a second resolution reuses the persisted wrapping.
    assert await store.get_project_data_key(oid, pa) == wrapped


async def test_project_key_cannot_decrypt_other_projects_ciphertext(store, master_key):
    oid, (pa, pb) = await _org_with_projects(store, "alpha", "bravo")
    ct_a = await encrypt_for_project(store, oid, pa, "alpha-only", master_key=master_key)

    # Project B's own data key cannot decrypt A's ciphertext.
    crypto_b = await project_crypto(store, oid, pb, master_key=master_key)
    with pytest.raises(SecretCorrupted):
        crypto_b.decrypt(ct_a)
    # ...and the same holds through the public decrypt API (B now has a key).
    with pytest.raises(SecretCorrupted):
        await decrypt_for_project(store, oid, pb, ct_a, master_key=master_key)


async def test_org_shared_secret_uses_master_key(store, master_key):
    oid, (pa,) = await _org_with_projects(store, "alpha")
    ct = await encrypt_for_project(store, oid, None, "org-wide", master_key=master_key)

    # Encrypted under the org master key directly — the org Crypto decrypts it.
    assert Crypto(master_key).decrypt(ct) == "org-wide"
    assert await decrypt_for_project(store, oid, None, ct, master_key=master_key) == "org-wide"
    # A project data key cannot decrypt an org-shared value.
    crypto_a = await project_crypto(store, oid, pa, master_key=master_key)
    with pytest.raises(SecretCorrupted):
        crypto_a.decrypt(ct)


async def test_idempotent_on_already_prefixed_values(store, master_key):
    oid, (pa,) = await _org_with_projects(store, "alpha")
    ct = await encrypt_for_project(store, oid, pa, "v", master_key=master_key)
    # Re-encrypting an already-``fernet:`` value is a no-op (no double-wrap).
    assert await encrypt_for_project(store, oid, pa, ct, master_key=master_key) == ct
    # Decrypting a non-prefixed (already-plaintext) value returns it unchanged.
    assert await decrypt_for_project(store, oid, pa, "plain", master_key=master_key) == "plain"


async def test_rotation_via_multifernet(store):
    oid, (pa,) = await _org_with_projects(store, "alpha")
    old_master = Fernet.generate_key()
    ct = await encrypt_for_project(store, oid, pa, "rotate-me", master_key=old_master)

    # Rotate the org master key: re-wrap the project data key under the new key,
    # keeping the old key available for the unwrap (MultiFernet).
    new_master = Fernet.generate_key()
    await rewrap_project_data_key(
        store, oid, pa, new_master_key=new_master, previous_master_keys=[old_master]
    )

    # The value still decrypts under the new master (the data key is unchanged).
    assert await decrypt_for_project(store, oid, pa, ct, master_key=new_master) == "rotate-me"
    # The old master alone can no longer unwrap the data key.
    with pytest.raises(SecretCorrupted):
        await decrypt_for_project(store, oid, pa, ct, master_key=old_master)


async def test_decrypt_for_project_without_key_raises(store, master_key):
    oid, (pa,) = await _org_with_projects(store, "alpha")
    # No data key has ever been minted for this project — decrypt does NOT mint
    # one on a read path, so there is nothing to decrypt with.
    with pytest.raises(SecretCorrupted):
        await decrypt_for_project(store, oid, pa, "fernet:whatever", master_key=master_key)


async def test_set_data_key_for_unknown_project_raises(store):
    oid, _ = await _org_with_projects(store)
    with pytest.raises(TenantAccessError):
        await store.set_project_data_key(oid, "ghost", "fernet:whatever")


async def test_set_data_key_if_absent_is_first_writer_wins(store):
    # The atomic primitive behind race-safe minting: a second writer never
    # clobbers the first, and both reads converge on the stored (winner's)
    # value. This is what makes concurrent first-use minting safe regardless of
    # how the read-then-write paths interleave.
    oid, (pa,) = await _org_with_projects(store, "alpha")
    first = await store.set_project_data_key_if_absent(oid, pa, "fernet:AAA")
    assert first == "fernet:AAA"
    second = await store.set_project_data_key_if_absent(oid, pa, "fernet:BBB")
    assert second == "fernet:AAA"  # not overwritten — winner is returned
    assert await store.get_project_data_key(oid, pa) == "fernet:AAA"
    # Unknown project still raises (mirrors set_project_data_key).
    with pytest.raises(TenantAccessError):
        await store.set_project_data_key_if_absent(oid, "ghost", "fernet:CCC")


async def test_concurrent_first_use_mints_one_stable_key(store, master_key):
    # P1 regression: several requests encrypting the first secrets for a
    # brand-new project must converge on ONE data key. A non-atomic
    # read-then-write would let each mint a different key, so the last writer
    # wins and every other request's ciphertext becomes undecryptable.
    oid, (pa,) = await _org_with_projects(store, "alpha")
    payloads = [f"secret-{i}" for i in range(6)]
    cts = await asyncio.gather(
        *(encrypt_for_project(store, oid, pa, p, master_key=master_key) for p in payloads)
    )
    # Every ciphertext decrypts back to its plaintext — no orphaned mint.
    for plain, ct in zip(payloads, cts):
        assert await decrypt_for_project(store, oid, pa, ct, master_key=master_key) == plain
    # Exactly one wrapped data key is persisted, and it is stable.
    assert await store.get_project_data_key(oid, pa) is not None


async def test_project_records_omit_wrapped_data_key(store, master_key):
    # P1 regression: generic project records must never carry the
    # ``fernet:``-wrapped data key (W0 secret-isolation gate), even after a key
    # has been minted — only the dedicated accessor exposes it.
    oid, (pa,) = await _org_with_projects(store, "alpha")
    await encrypt_for_project(store, oid, pa, "secret", master_key=master_key)

    proj = await store.get_project(oid, pa)
    assert proj is not None
    assert "data_key_ref" not in proj
    assert not any(isinstance(v, str) and v.startswith(Crypto.PREFIX) for v in proj.values())

    listed = await store.list_projects(oid)
    assert listed and all("data_key_ref" not in p for p in listed)

    # The wrapped key is still reachable through the dedicated key accessor.
    assert await store.get_project_data_key(oid, pa) is not None
