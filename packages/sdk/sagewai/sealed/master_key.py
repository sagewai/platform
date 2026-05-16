# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Master-key resolution chain — env-var → keychain → file."""
from __future__ import annotations

import os
import stat
from pathlib import Path

try:
    import keyring
except ImportError:
    keyring = None  # optional dep

DEFAULT_KEY_PATH = Path.home() / ".sagewai" / "master.key"
KEYRING_SERVICE = "sagewai-master-key"
KEYRING_USERNAME = "sealed"


class MasterKeyMissing(RuntimeError):  # noqa: N818
    """Raised when no master key can be resolved."""


def resolve_master_key() -> tuple[bytes, str]:
    """Resolve the master key. Returns (key_bytes, source_label).

    Order: env-var → OS keychain → ~/.sagewai/master.key file.
    Raises MasterKeyMissing if none configured.
    """
    env_key = os.environ.get("SAGEWAI_MASTER_KEY")
    if env_key:
        return _normalize_key(env_key), "env-var"

    if keyring is not None:
        try:
            kr_value = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            kr_value = None
        if kr_value:
            return _normalize_key(kr_value), "keychain"

    if DEFAULT_KEY_PATH.exists():
        _verify_secure_perms(DEFAULT_KEY_PATH)
        file_value = DEFAULT_KEY_PATH.read_text(encoding="utf-8").strip()
        return _normalize_key(file_value), "file"

    raise MasterKeyMissing(
        "No SAGEWAI_MASTER_KEY env var, OS keychain entry, or master.key file found. "
        "Run `sagewai admin sealed init` to create one."
    )


def _normalize_key(raw: str) -> bytes:
    """Validate Fernet-format key (44 chars urlsafe-b64).

    Length-only check; the base64 charset is validated downstream by the
    Fernet constructor.
    """
    raw_bytes = raw.encode("ascii")
    if len(raw_bytes) != 44:
        raise MasterKeyMissing(
            f"master key must be 44 chars (Fernet format), got {len(raw_bytes)} chars"
        )
    return raw_bytes


def _verify_secure_perms(path: Path) -> None:
    """Refuse to read a key file with permissive perms."""
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
        raise MasterKeyMissing(
            f"{path} has insecure permissions (mode {oct(stat.S_IMODE(mode))}); "
            f"chmod 0600 to fix"
        )


def store_master_key(
    key: bytes,
    destination: str,
    *,
    path: Path | None = None,
) -> None:
    """Write a master key to the chosen destination."""
    if len(key) != 44:
        raise ValueError(
            f"master key must be 44 chars (Fernet format), got {len(key)} chars"
        )
    if destination == "env-var":
        # Print instructions; can't actually set parent shell env vars.
        return
    elif destination == "keychain":
        if keyring is None:
            raise RuntimeError(
                "keyring not installed; install with `pip install sagewai[keychain]`"
            )
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.decode("ascii"))
    elif destination == "file":
        target = path or DEFAULT_KEY_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(key)
        target.chmod(0o600)
    else:
        raise ValueError(f"unknown destination {destination!r}")
