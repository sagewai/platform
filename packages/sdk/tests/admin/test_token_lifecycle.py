# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

from __future__ import annotations
import json
from pathlib import Path
import pytest
from sagewai.admin.state_file import AdminStateFile


def _seed(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


def test_run_migrations_is_versioned_and_idempotent(tmp_path):
    p = tmp_path / "admin-state.json"
    _seed(p, {"setup_complete": True})            # schema_version absent == 0
    sf = AdminStateFile(path=p)

    sf.run_migrations()
    after = json.loads(p.read_text())
    assert after["schema_version"] == sf.SCHEMA_VERSION

    # second run is a no-op (no exception, version unchanged)
    sf.run_migrations()
    assert json.loads(p.read_text())["schema_version"] == sf.SCHEMA_VERSION


def test_run_migrations_backs_up_before_changing(tmp_path):
    p = tmp_path / "admin-state.json"
    _seed(p, {"setup_complete": True})
    AdminStateFile(path=p).run_migrations()
    backups = list(tmp_path.glob("admin-state.json.bak-v0-*"))
    assert len(backups) >= 1, "expected at least one timestamped backup"


def test_run_migrations_restores_on_step_failure(tmp_path):
    p = tmp_path / "admin-state.json"
    _seed(p, {"setup_complete": True})
    original = p.read_text()

    def _bad_step(data):
        data["corrupted"] = True
        raise RuntimeError("boom")

    import sagewai.admin.state_file as sf_mod
    saved = sf_mod._MIGRATION_STEPS
    sf_mod._MIGRATION_STEPS = [_bad_step]
    try:
        with pytest.raises(RuntimeError):
            AdminStateFile(path=p).run_migrations()
    finally:
        sf_mod._MIGRATION_STEPS = saved

    assert p.read_text() == original
    assert "corrupted" not in json.loads(p.read_text())


def test_repeated_failed_migrations_preserve_distinct_backups(tmp_path):
    import sagewai.admin.state_file as sfmod

    p = tmp_path / "admin-state.json"
    _seed(p, {"setup_complete": True})

    def _boom(d):
        raise RuntimeError("boom")

    saved = sfmod._MIGRATION_STEPS
    sfmod._MIGRATION_STEPS = [_boom]
    try:
        for _ in range(2):
            try:
                AdminStateFile(path=p).run_migrations()
            except RuntimeError:
                pass
    finally:
        sfmod._MIGRATION_STEPS = saved

    backups = list(tmp_path.glob("admin-state.json.bak-v0-*"))
    assert len(backups) >= 2, f"expected distinct backups, got {backups}"


def _make_setup(path):
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    return sf


def test_login_returns_raw_but_stores_only_hash(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    out = sf.validate_login("a@b.com", "pw123456")
    raw = out["access_token"]
    stored = json.loads((tmp_path / "s.json").read_text())["active_tokens"]
    assert all(raw != rec["hash"] for rec in stored)   # raw never stored
    assert sf.validate_token(raw) is True               # but validates


def test_expired_token_is_rejected(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    data = sf._read()
    data["active_tokens"][-1]["expires_at"] = "2000-01-01T00:00:00+00:00"
    sf._write(data)
    assert sf.validate_token(raw) is False
    assert sf.get_user_by_token(raw) is None


def test_logout_revokes_server_side(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    assert sf.revoke_session_token(raw) is True
    assert sf.validate_token(raw) is False


def test_refresh_rotates_and_old_still_valid_until_expiry(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    out = sf.refresh_token(raw)
    assert out is not None and out["access_token"] != raw
    assert sf.validate_token(out["access_token"]) is True
    assert sf.validate_token(raw) is True   # old stays valid (intentional overlap)


def test_migration_hashes_legacy_string_tokens(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "setup_complete": True,
        "active_tokens": ["legacy-raw-token"],
        "api_tokens": [{"id": "tok-1", "token": "sw_legacy", "prefix": "sw_legacy123",
                        "scopes": ["read"], "revoked": False}],
    }))
    sf = AdminStateFile(path=p)
    sf.run_migrations()
    data = sf._read()
    assert isinstance(data["active_tokens"][0], dict)
    assert "hash" in data["active_tokens"][0]
    assert "token" not in data["api_tokens"][0]   # raw removed
    assert "token_hash" in data["api_tokens"][0]


def test_mutate_does_not_lose_concurrent_updates(tmp_path):
    import threading
    p = tmp_path / "admin-state.json"
    p.write_text('{"counter": 0}')
    sf = AdminStateFile(path=p)

    def bump():
        for _ in range(50):
            sf._mutate(lambda d: d.__setitem__("counter", d.get("counter", 0) + 1))

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert json.loads(p.read_text())["counter"] == 200


def test_record_is_live_false_on_malformed_expiry():
    from sagewai.admin.state_file import _record_is_live
    assert _record_is_live({"expires_at": "not-a-date"}) is False
    assert _record_is_live({"expires_at": None}) is True


def test_api_token_create_returns_raw_once_then_only_prefix(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    created = sf.create_api_token(name="CI", scopes=["read", "write"])
    raw = created["token"]
    assert raw.startswith("sw_")
    listed = sf.list_api_tokens()
    assert all("token" not in t and "token_hash" not in t for t in listed)  # no secret material
    assert listed[0]["prefix"] == raw[:12]
    found = sf.find_api_token(raw)
    assert found is not None and set(found["scopes"]) == {"read", "write"}
    assert "token_hash" not in found


def test_revoked_api_token_is_not_found(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    created = sf.create_api_token(name="CI", scopes=["read"])
    assert sf.revoke_api_token(created["id"]) is True
    assert sf.find_api_token(created["token"]) is None


def test_delete_api_token_removes_it(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    created = sf.create_api_token(name="CI", scopes=["read"])
    sf.delete_api_token(created["id"])
    assert sf.list_api_tokens() == []
