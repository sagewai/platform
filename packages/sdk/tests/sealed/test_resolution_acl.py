# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cascade merge: ACL flows through cascade levels with later-wins per tool."""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.models import ProfileWritePayload
from sagewai.sealed.refs import register_backend
from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile


@pytest.fixture(autouse=True)
def clear_registry():
    from sagewai.sealed.refs import _BACKENDS
    saved = _BACKENDS.copy()
    _BACKENDS.clear()
    yield
    _BACKENDS.clear()
    _BACKENDS.update(saved)


@pytest.mark.asyncio
async def test_acl_populated_from_single_level(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    backend = BuiltinAdminStoreBackend(profiles_path=tmp_path / "p.json", crypto=Crypto(key))
    await backend.save_profile(ProfileWritePayload(
        id="p1", name="P", secrets={"K": "v"},
        acl={"claude-code": ["K"]},
    ))
    register_backend(backend)
    ep = await resolve_security_profile(
        levels=[CascadeLevel(name="user", profile_ref="p1", overrides=None)],
    )
    assert ep.acl == {"claude-code": ["K"]}


@pytest.mark.asyncio
async def test_workflow_acl_replaces_system_per_tool(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    backend = BuiltinAdminStoreBackend(profiles_path=tmp_path / "p.json", crypto=Crypto(key))
    await backend.save_profile(ProfileWritePayload(
        id="sys", name="System Profile",
        secrets={"K1": "v1", "K2": "v2"},
        acl={
            "claude-code": ["K1"],   # system says: claude-code can see K1
            "codex":       ["K2"],   # system says: codex can see K2
        },
    ))
    await backend.save_profile(ProfileWritePayload(
        id="wf", name="Workflow Profile",
        secrets={"K1": "v1"},
        acl={
            "claude-code": ["K1", "K2"],  # workflow widens for claude-code (replace)
            # codex not mentioned at workflow level; system rule survives
        },
    ))
    register_backend(backend)
    ep = await resolve_security_profile(
        levels=[
            CascadeLevel(name="system", profile_ref="sys", overrides=None),
            CascadeLevel(name="workflow", profile_ref="wf", overrides=None),
        ],
    )
    assert ep.acl == {
        "claude-code": ["K1", "K2"],   # workflow won
        "codex":       ["K2"],          # system preserved
    }


@pytest.mark.asyncio
async def test_empty_workflow_acl_list_overrides_system_allowlist(tmp_path: Path) -> None:
    key = Fernet.generate_key()
    backend = BuiltinAdminStoreBackend(profiles_path=tmp_path / "p.json", crypto=Crypto(key))
    await backend.save_profile(ProfileWritePayload(
        id="sys", name="System",
        secrets={"K": "v"}, acl={"claude-code": ["K"]},
    ))
    await backend.save_profile(ProfileWritePayload(
        id="wf", name="WF",
        secrets={"K": "v"}, acl={"claude-code": []},  # explicit deny-all at workflow level
    ))
    register_backend(backend)
    ep = await resolve_security_profile(
        levels=[
            CascadeLevel(name="system", profile_ref="sys", overrides=None),
            CascadeLevel(name="workflow", profile_ref="wf", overrides=None),
        ],
    )
    assert ep.acl == {"claude-code": []}
