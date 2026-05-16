# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""BuiltinAdminStoreBackend — encrypted JSON file at ~/.sagewai/profiles.json."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sagewai.sealed.audit import AuditWriter
from sagewai.sealed.backend import (
    BackendUnsupportedOperationError,
    ProfileNotFoundError,
)
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.models import Profile, ProfileMetadata, ProfileWritePayload

_DEFAULT_PATH = Path.home() / ".sagewai" / "profiles.json"


class BuiltinAdminStoreBackend:
    """Profile backend backed by a Fernet-encrypted JSON file."""

    name = "builtin"
    scheme = "builtin"

    def __init__(
        self,
        profiles_path: Path | None = None,
        crypto: Crypto | None = None,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._path = profiles_path or _DEFAULT_PATH
        self._crypto = crypto
        self._audit = audit_writer
        self._lock = asyncio.Lock()

    def _get_crypto(self) -> Crypto:
        if self._crypto is None:
            from sagewai.sealed.master_key import resolve_master_key
            key, _ = resolve_master_key()
            self._crypto = Crypto(key)
        return self._crypto

    def _read_store(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": 1, "profiles": []}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write_store(self, store: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: tmpfile + fsync + rename
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=self._path.parent, prefix=".profiles.", suffix=".tmp",
        ) as tmp:
            json.dump(store, tmp, indent=2, default=str)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self._path)

    @staticmethod
    def _to_metadata(p: dict[str, Any]) -> ProfileMetadata:
        return ProfileMetadata(
            id=p["id"],
            name=p["name"],
            description=p.get("description", ""),
            owner=p.get("owner"),
            tags=p.get("tags", []),
            last_rotated_at=(
                datetime.fromisoformat(p["last_rotated_at"])
                if p.get("last_rotated_at") else None
            ),
            allowed_workflows=p.get("allowed_workflows", []),
            env=p.get("env", {}),
            secret_keys=sorted(p.get("secrets", {}).keys()),
            acl=p.get("acl", {}),
        )

    async def list_profiles(self) -> list[ProfileMetadata]:
        async with self._lock:
            store = self._read_store()
        return [self._to_metadata(p) for p in store["profiles"]]

    async def get_profile_metadata(self, profile_id: str) -> ProfileMetadata:
        async with self._lock:
            store = self._read_store()
        for p in store["profiles"]:
            if p["id"] == profile_id:
                return self._to_metadata(p)
        raise ProfileNotFoundError(profile_id)

    async def get_profile(self, profile_id: str) -> Profile:
        async with self._lock:
            store = self._read_store()
        for p in store["profiles"]:
            if p["id"] == profile_id:
                decrypted: dict[str, str] = {}
                for key, ciphertext in p.get("secrets", {}).items():
                    decrypted[key] = self._get_crypto().decrypt(ciphertext)
                    if self._audit:
                        await self._audit.emit(
                            event_type="secret.decrypted",
                            profile_id=profile_id,
                            secret_key=key,
                            details={"purpose": "get_profile"},
                        )
                metadata = self._to_metadata(p)
                return Profile(**metadata.model_dump(), secrets=decrypted)
        raise ProfileNotFoundError(profile_id)

    async def save_profile(self, payload: ProfileWritePayload) -> Profile:
        if not payload.id:
            raise ValueError("ProfileWritePayload.id is required for save_profile")
        async with self._lock:
            store = self._read_store()
            existing_idx = next(
                (i for i, p in enumerate(store["profiles"]) if p["id"] == payload.id),
                None,
            )
            now = datetime.now(timezone.utc).isoformat()
            profile_dict = {
                "id": payload.id,
                "name": payload.name,
                "description": payload.description,
                "owner": payload.owner,
                "tags": payload.tags,
                "last_rotated_at": now,
                "allowed_workflows": payload.allowed_workflows,
                "env": payload.env,
                "acl": payload.acl,
                "secrets": {
                    k: self._get_crypto().encrypt(v)
                    for k, v in payload.secrets.items()
                },
            }
            event_type = "profile.created"
            if existing_idx is not None:
                store["profiles"][existing_idx] = profile_dict
                event_type = "profile.updated"
            else:
                store["profiles"].append(profile_dict)
            self._write_store(store)

        if self._audit:
            await self._audit.emit(
                event_type=event_type,
                profile_id=payload.id,
                details={
                    "name": payload.name,
                    "secret_keys": sorted(payload.secrets.keys()),
                    "env_keys": sorted(payload.env.keys()),
                },
            )
        return await self.get_profile(payload.id)

    async def delete_profile(self, profile_id: str) -> None:
        async with self._lock:
            store = self._read_store()
            before = len(store["profiles"])
            store["profiles"] = [p for p in store["profiles"] if p["id"] != profile_id]
            if len(store["profiles"]) == before:
                raise ProfileNotFoundError(profile_id)
            self._write_store(store)
        if self._audit:
            await self._audit.emit(
                event_type="profile.deleted",
                profile_id=profile_id,
            )

    async def supports_value_history(self) -> bool:
        return False

    async def get_secret_at_version(
        self,
        profile_id: str,
        secret_key: str,
        version_id: str,
    ) -> str:
        raise BackendUnsupportedOperationError(
            "Builtin backend has no value history; rotation replaces "
            "the value in place. Use Vault/SOPS/AWS-SM via Sealed-ii "
            "for replay-after-rotation support."
        )

    async def supports_master_key_rotation(self) -> bool:
        return True

    async def rotate_master_key(self, new_key: bytes) -> int:
        async with self._lock:
            store = self._read_store()
            old_crypto = self._get_crypto()
            new_crypto = Crypto(new_key)
            count = 0
            for p in store["profiles"]:
                for key, ct in p.get("secrets", {}).items():
                    plaintext = old_crypto.decrypt(ct)
                    p["secrets"][key] = new_crypto.encrypt(plaintext)
                    count += 1
            self._write_store(store)
            self._crypto = new_crypto
        return count
