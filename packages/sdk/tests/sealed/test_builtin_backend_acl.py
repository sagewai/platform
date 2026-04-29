# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""ACL round-trips through the encrypted JSON file backend."""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.models import ProfileWritePayload


@pytest.mark.asyncio
async def test_acl_round_trips_through_save_load(tmp_path: Path) -> None:
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
    )
    await backend.save_profile(ProfileWritePayload(
        id="p1",
        name="P One",
        secrets={"K1": "v1", "K2": "v2"},
        env={"DEBUG": "1"},
        acl={"claude-code": ["K1"], "codex": ["K2"], "shell": []},
    ))

    p = await backend.get_profile("p1")
    assert p.acl == {"claude-code": ["K1"], "codex": ["K2"], "shell": []}

    pm = await backend.get_profile_metadata("p1")
    assert pm.acl == {"claude-code": ["K1"], "codex": ["K2"], "shell": []}


@pytest.mark.asyncio
async def test_acl_default_empty_when_unset(tmp_path: Path) -> None:
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(Fernet.generate_key()),
    )
    await backend.save_profile(ProfileWritePayload(
        id="p1", name="P One", secrets={"K": "v"},
    ))

    p = await backend.get_profile("p1")
    assert p.acl == {}


@pytest.mark.asyncio
async def test_acl_survives_master_key_rotation(tmp_path: Path) -> None:
    key1 = Fernet.generate_key()
    backend = BuiltinAdminStoreBackend(
        profiles_path=tmp_path / "profiles.json",
        crypto=Crypto(key1),
    )
    await backend.save_profile(ProfileWritePayload(
        id="p1", name="P One",
        secrets={"K": "v"}, acl={"shell": []},
    ))
    key2 = Fernet.generate_key()
    await backend.rotate_master_key(key2)

    p = await backend.get_profile("p1")
    assert p.acl == {"shell": []}
    assert p.secrets == {"K": "v"}
