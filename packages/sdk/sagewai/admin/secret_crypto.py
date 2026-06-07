# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Env-aware master-key resolution for admin at-rest encryption (spec §3.D)."""
from __future__ import annotations

import os

from sagewai.sealed.crypto import Crypto
from sagewai.sealed.master_key import (
    MasterKeyMissing,
    resolve_master_key,
    store_master_key,
)


class AdminKeyMissing(RuntimeError):  # noqa: N818
    """No master key and we refuse to auto-provision (container/ephemeral runtime)."""


def _is_container() -> bool:
    return os.environ.get("SAGEWAI_RUNTIME", "") == "container"


def get_admin_crypto() -> Crypto:
    """Return a Crypto for admin at-rest encryption.

    Resolves the master key (env → keychain → file). If none and NOT a container,
    auto-provisions a local 0600 key file (zero-config). In a container with no
    key, fails closed — losing an ephemeral key would make secrets unrecoverable.
    """
    try:
        key, _ = resolve_master_key()
    except MasterKeyMissing:
        if _is_container():
            raise AdminKeyMissing(
                "No SAGEWAI_MASTER_KEY in a container runtime; encrypted provider "
                "secrets require a persistent key. Set SAGEWAI_MASTER_KEY or mount "
                "a key file on a persistent volume."
            ) from None
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        store_master_key(key, "file")  # writes 0600 to home.secrets_dir()/master.key
    return Crypto(key)
