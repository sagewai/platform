# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Instance identity for the Sagewai LLM client.

Each Sagewai install is identified to the hosted service by an
:class:`InstanceIdentity`: a randomly generated hex ``instance_id``
(used as the rate-limit key) and a randomly generated hex
``instance_secret`` (used as the HMAC signing key). The secret NEVER
leaves the client — it is only used to sign outgoing requests.

Storage is abstracted through :class:`InstanceIdentityStore` so that
Plan 7 can swap the default :class:`FileIdentityStore` for the admin
state file without touching the client. The default file store writes
a JSON file with restrictive permissions to a path chosen by the caller.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class InstanceIdentity:
    """One Sagewai install's identity for the hosted service."""

    instance_id: str  # 32-char hex (16 bytes)
    instance_secret: str  # 64-char hex (32 bytes)

    @classmethod
    def generate(cls) -> InstanceIdentity:
        return cls(
            instance_id=uuid.uuid4().hex,
            instance_secret=secrets.token_hex(32),
        )


@runtime_checkable
class InstanceIdentityStore(Protocol):
    """Storage abstraction for :class:`InstanceIdentity`."""

    def load(self) -> InstanceIdentity | None: ...

    def save(self, identity: InstanceIdentity) -> None: ...


class FileIdentityStore:
    """JSON-file-backed :class:`InstanceIdentityStore`.

    The file is created with mode 0o600 so only the current user can
    read the secret. Parent directories are created on first save.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> InstanceIdentity | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        iid = data.get("instance_id")
        sec = data.get("instance_secret")
        if not (isinstance(iid, str) and isinstance(sec, str)):
            return None
        return InstanceIdentity(instance_id=iid, instance_secret=sec)

    def save(self, identity: InstanceIdentity) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "instance_id": identity.instance_id,
                "instance_secret": identity.instance_secret,
            },
            indent=2,
        )
        self._path.write_text(payload, encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            # Windows and some test environments don't support chmod;
            # it's not fatal for correctness, just for secrecy.
            pass


def ensure_identity(store: InstanceIdentityStore) -> InstanceIdentity:
    """Load an identity from ``store`` or generate and persist a new one."""
    existing = store.load()
    if existing is not None:
        return existing
    fresh = InstanceIdentity.generate()
    store.save(fresh)
    return fresh
