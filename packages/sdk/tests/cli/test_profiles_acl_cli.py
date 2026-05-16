# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai admin sealed profiles acl show/set/remove subcommands."""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from cryptography.fernet import Fernet

from sagewai.cli.profiles import profiles_group
from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto


def test_acl_show_then_set_then_remove(tmp_path: Path, monkeypatch) -> None:
    """Test acl subcommand: show, set, remove."""
    # Setup temp profiles path and custom backend
    profiles_path = tmp_path / "p.json"
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", "Tg2K2sXBmxGMV5p1u9zoRm0vAH8kN7eD0YJN_pCJzxw=")

    # Create a custom backend that uses the temp path
    crypto = Crypto(Fernet.generate_key())
    custom_backend = BuiltinAdminStoreBackend(
        profiles_path=profiles_path,
        crypto=crypto,
    )

    # Patch the resolve_backend function to return our custom backend
    from sagewai.sealed.refs import ProfileRef
    def mock_resolve_backend(ref: ProfileRef):
        return custom_backend

    monkeypatch.setattr("sagewai.cli.profiles.resolve_backend", mock_resolve_backend)

    runner = CliRunner()

    # Seed a profile via direct file write
    raw = {
        "version": 1,
        "profiles": [{
            "id": "test",
            "name": "Test",
            "description": "",
            "owner": None,
            "tags": [],
            "last_rotated_at": "2026-04-27T00:00:00+00:00",
            "allowed_workflows": [],
            "env": {"DEBUG": "1"},
            "secrets": {},
            "acl": {},
        }],
    }
    profiles_path.parent.mkdir(parents=True, exist_ok=True)
    profiles_path.write_text(json.dumps(raw))

    # show — empty
    res = runner.invoke(profiles_group, ["acl", "test", "show"])
    assert res.exit_code == 0, f"Exit {res.exit_code}: {res.output}"
    assert res.output.strip() in ("{}", "{\n}")

    # set
    res = runner.invoke(profiles_group, ["acl", "test", "set", "claude-code", "K1,K2"])
    assert res.exit_code == 0, f"Exit {res.exit_code}: {res.output}"
    res = runner.invoke(profiles_group, ["acl", "test", "show"])
    out = json.loads(res.output)
    assert out == {"claude-code": ["K1", "K2"]}

    # set deny-all (empty arg)
    res = runner.invoke(profiles_group, ["acl", "test", "set", "shell", ""])
    assert res.exit_code == 0, f"Exit {res.exit_code}: {res.output}"
    res = runner.invoke(profiles_group, ["acl", "test", "show"])
    out = json.loads(res.output)
    assert out == {"claude-code": ["K1", "K2"], "shell": []}

    # remove
    res = runner.invoke(profiles_group, ["acl", "test", "remove", "shell"])
    assert res.exit_code == 0, f"Exit {res.exit_code}: {res.output}"
    res = runner.invoke(profiles_group, ["acl", "test", "show"])
    out = json.loads(res.output)
    assert out == {"claude-code": ["K1", "K2"]}
