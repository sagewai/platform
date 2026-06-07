# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-project encryption keys (W5 of the multi-tenancy/RBAC roadmap).

Envelope encryption that makes the crypto boundary match the tenancy boundary
(W0 RFC §7). Each project owns a random Fernet **data key**; that data key is
wrapped (encrypted) under the org **master key** and the wrapped blob is the
only thing persisted, in ``project.data_key_ref`` via :class:`IdentityStore`.
Because a project's secrets are encrypted under its own data key, a leaked
project key cannot decrypt another project's secrets. **Org-shared** secrets
(``project_id is None``) are encrypted under the org master key directly.

This module reuses the Sealed :class:`~sagewai.sealed.crypto.Crypto`
abstraction for every crypto operation — including the data-key wrapping — so
``MultiFernet`` rotation (:meth:`Crypto.rotate_value`) carries over for free:
rotating the org master key is just re-wrapping each project's data key
(:func:`rewrap_project_data_key`); the data key itself is unchanged, so the
project's ciphertexts keep decrypting.

Master-key custody
------------------
The org master key is resolved through the Sealed chain
(:func:`~sagewai.sealed.master_key.resolve_master_key`): env-var → OS keychain
→ ``$SAGEWAI_HOME/secrets/master.key``. :func:`set_master_key_source` is the
clean extension point for an **optional Vault** source (it would return
``(key_bytes, "vault")`` and could key off ``org.master_key_ref``). No
cloud-specific KMS — see the RFC's KMS decision (cloud-agnostic).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet

from sagewai.sealed.crypto import Crypto, SecretCorrupted
from sagewai.sealed.master_key import resolve_master_key

if TYPE_CHECKING:
    from sagewai.admin.identity_store import IdentityStore

# A master-key source returns ``(key_bytes, source_label)`` — the same shape as
# ``resolve_master_key``. This indirection is the Vault extension point: install
# a Vault-backed source with ``set_master_key_source`` without touching any call
# site. Deliberately NOT a cloud KMS.
MasterKeySource = Callable[[], "tuple[bytes, str]"]

_master_key_source: MasterKeySource = resolve_master_key


class ProjectKeyMissing(SecretCorrupted):
    """Raised when a project has no data key yet, so nothing can be decrypted.

    Subclasses :class:`SecretCorrupted` so callers that only care that a value
    could not be decrypted can catch the base class, while custody-aware code
    can distinguish "no key minted" from "wrong key / tampered".
    """


def set_master_key_source(source: MasterKeySource) -> None:
    """Install a custom org-master-key source (e.g. a Vault source). No KMS."""
    global _master_key_source
    _master_key_source = source


def reset_master_key_source() -> None:
    """Restore the default env→keychain→file master-key resolution chain."""
    global _master_key_source
    _master_key_source = resolve_master_key


def org_crypto(*, master_key: bytes | None = None) -> Crypto:
    """The org master :class:`Crypto`.

    Encrypts org-shared secrets and wraps/unwraps per-project data keys.
    ``master_key`` overrides resolution (tests, or an injected custody value);
    otherwise the configured source (default: the Sealed chain) is used.
    """
    if master_key is None:
        master_key, _source = _master_key_source()
    return Crypto(master_key)


async def project_crypto(
    store: IdentityStore,
    org_id: str,
    project_id: str,
    *,
    master_key: bytes | None = None,
    create: bool = True,
) -> Crypto:
    """Resolve a project-scoped :class:`Crypto` by unwrapping its data key.

    The wrapped data key is read from ``project.data_key_ref`` and decrypted
    under the org master key. When the project has no data key yet and
    ``create`` is true, a fresh random Fernet data key is generated, wrapped
    under the org master key, and persisted via an atomic if-absent write so
    concurrent first-use callers converge on one stable key (the loser discards
    its candidate and unwraps the winner's). With ``create`` false a missing key
    raises :class:`ProjectKeyMissing` (no key is ever minted on a read path).
    """
    master = org_crypto(master_key=master_key)
    wrapped = await store.get_project_data_key(org_id, project_id)
    if wrapped is None:
        if not create:
            raise ProjectKeyMissing(f"project {project_id!r} has no data key")
        candidate = master.encrypt(Fernet.generate_key().decode("ascii"))
        # Atomic mint: if a racing caller already stored a key, this returns
        # theirs instead of ours, so both sides use the same data key.
        wrapped = await store.set_project_data_key_if_absent(org_id, project_id, candidate)
    data_key = master.decrypt(wrapped).encode("ascii")
    return Crypto(data_key)


async def encrypt_for_project(
    store: IdentityStore,
    org_id: str,
    project_id: str | None,
    plaintext: str,
    *,
    master_key: bytes | None = None,
) -> str:
    """Encrypt ``plaintext`` for a project (or org-shared when ``project_id`` is None).

    Idempotent: a value already carrying the ``fernet:`` prefix is returned
    unchanged (never double-wrapped). Project secrets are encrypted under the
    project data key (minted on first use); org-shared secrets under the org
    master key.
    """
    if plaintext.startswith(Crypto.PREFIX):
        return plaintext
    crypto = (
        org_crypto(master_key=master_key)
        if project_id is None
        else await project_crypto(store, org_id, project_id, master_key=master_key, create=True)
    )
    return crypto.encrypt(plaintext)


async def decrypt_for_project(
    store: IdentityStore,
    org_id: str,
    project_id: str | None,
    ciphertext: str,
    *,
    master_key: bytes | None = None,
) -> str:
    """Decrypt a project (or org-shared) value produced by :func:`encrypt_for_project`.

    Idempotent: a value without the ``fernet:`` prefix is assumed to be plain
    and returned unchanged. Decrypt never mints a data key — a project with no
    key raises :class:`ProjectKeyMissing` (a :class:`SecretCorrupted`).
    """
    if not ciphertext.startswith(Crypto.PREFIX):
        return ciphertext
    crypto = (
        org_crypto(master_key=master_key)
        if project_id is None
        else await project_crypto(store, org_id, project_id, master_key=master_key, create=False)
    )
    return crypto.decrypt(ciphertext)


async def rewrap_project_data_key(
    store: IdentityStore,
    org_id: str,
    project_id: str,
    *,
    new_master_key: bytes,
    previous_master_keys: Iterable[bytes] | None = None,
) -> str:
    """Re-wrap a project's data key under a rotated org master key (MultiFernet).

    Used during org master-key rotation. ``previous_master_keys`` must include
    the key the data key is currently wrapped under so the unwrap succeeds; the
    data key is then re-wrapped under ``new_master_key``. The data key itself is
    unchanged, so every value the project already encrypted keeps decrypting.
    Returns the new wrapped blob.
    """
    rotator = Crypto(new_master_key, previous_keys=list(previous_master_keys or ()))
    wrapped = await store.get_project_data_key(org_id, project_id)
    if wrapped is None:
        raise ProjectKeyMissing(f"project {project_id!r} has no data key to re-wrap")
    new_wrapped = rotator.rotate_value(wrapped)
    await store.set_project_data_key(org_id, project_id, new_wrapped)
    return new_wrapped


__all__ = [
    "MasterKeySource",
    "ProjectKeyMissing",
    "decrypt_for_project",
    "encrypt_for_project",
    "org_crypto",
    "project_crypto",
    "reset_master_key_source",
    "rewrap_project_data_key",
    "set_master_key_source",
]
