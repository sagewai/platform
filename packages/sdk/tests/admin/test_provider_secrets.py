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
import pytest
from cryptography.fernet import Fernet

# ── provider_secrets unit tests ──────────────────────────────────────


def test_walk_finds_nested_and_list_secrets():
    from sagewai.admin.provider_secrets import walk_secret_fields
    cfg = {"api_key": "sk-1", "nested": {"client_secret": "cs"}, "items": [{"token": "t"}]}
    seen = []
    walk_secret_fields(cfg, lambda parent, k: seen.append((k, parent[k])))
    assert ("api_key", "sk-1") in seen
    assert ("client_secret", "cs") in seen
    assert ("token", "t") in seen


def test_redact_strips_and_flags():
    from sagewai.admin.provider_secrets import redact_secrets
    rec = {"config": {"api_key": "sk-1", "model": "gpt"}}
    out = redact_secrets(rec)
    assert "api_key" not in out["config"]
    assert out["config"]["api_key_set"] is True
    assert out["config"]["model"] == "gpt"
    assert rec["config"]["api_key"] == "sk-1"  # original untouched (deep copy)


def test_provider_secret_encrypted_at_rest_and_redacted_in_api(tmp_path):
    from sagewai.admin.state_file import AdminStateFile
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "sk-live-XYZ"}})
    raw = (tmp_path / "s.json").read_text()
    assert "sk-live-XYZ" not in raw                  # not plaintext on disk
    assert "fernet:" in raw                           # encrypted marker present
    listed = sf.list_providers()
    blob = str(listed)
    assert "sk-live-XYZ" not in blob and "fernet:" not in blob   # neither plaintext nor ciphertext in API
    assert listed[0]["config"].get("api_key_set") is True
    assert "api_key" not in listed[0]["config"]


def test_provider_decrypt_for_internal_use(tmp_path):
    from sagewai.admin.state_file import AdminStateFile
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "sk-live-XYZ"}})
    rec = sf.get_provider_decrypted("openai")
    assert rec["config"]["api_key"] == "sk-live-XYZ"


def test_migration_v2_encrypts_existing_plaintext(tmp_path):
    import json
    from sagewai.admin.state_file import AdminStateFile
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"schema_version": 1, "providers": [
        {"provider_name": "openai", "id": "prov-openai", "config": {"api_key": "sk-old-PLAIN"}}]}))
    sf = AdminStateFile(path=p)
    sf.run_migrations()
    assert "sk-old-PLAIN" not in p.read_text()
    assert sf.get_provider_decrypted("openai")["config"]["api_key"] == "sk-old-PLAIN"


def test_admin_crypto_roundtrip_with_env_key(monkeypatch):
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    from sagewai.admin.secret_crypto import get_admin_crypto
    c = get_admin_crypto()
    ct = c.encrypt("sk-secret")
    assert ct.startswith("fernet:") and c.decrypt(ct) == "sk-secret"


def test_admin_crypto_fails_closed_in_container_without_key(monkeypatch, tmp_path):
    from pathlib import Path
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setenv("SAGEWAI_RUNTIME", "container")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))   # empty home → no key resolves
    from sagewai.admin.secret_crypto import get_admin_crypto, AdminKeyMissing
    with pytest.raises(AdminKeyMissing):
        get_admin_crypto()


def test_list_providers_decrypted_falls_back_without_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from sagewai.admin.state_file import AdminStateFile
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "sk-XYZ"}})
    # drop the key + force container + ephemeral HOME so get_admin_crypto fails closed
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setenv("SAGEWAI_RUNTIME", "container")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "nohome"))   # empty home → no key resolves
    out = sf.list_providers_decrypted()
    assert out[0]["config"]["api_key"] is None  # nulled, never ciphertext


def test_no_provisioning_when_nothing_encrypted(tmp_path, monkeypatch):
    from sagewai.admin.state_file import AdminStateFile
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setenv("SAGEWAI_RUNTIME", "container")  # would fail-closed IF a key were requested
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "nohome"))   # empty home → no key resolves
    sf = AdminStateFile(path=tmp_path / "s.json")  # no providers
    assert sf.list_providers_decrypted() == []      # must NOT call get_admin_crypto / raise


def test_decrypt_degrades_on_wrong_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from sagewai.admin.state_file import AdminStateFile
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "sk-XYZ"}})
    # rotate to a DIFFERENT key — old ciphertext can't be decrypted
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    out = sf.list_providers_decrypted()                 # must NOT raise
    assert out[0]["config"]["api_key"] is None           # nulled, never ciphertext
    rec = sf.get_provider_decrypted("openai")           # must NOT raise
    assert rec["config"]["api_key"] is None


def test_database_url_env_accepted_either_name(monkeypatch):
    from sagewai.admin import serve as srv
    monkeypatch.delenv("SAGEWAI_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert srv._resolve_database_url() is None
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert srv._resolve_database_url() == "postgresql://x"
    monkeypatch.setenv("SAGEWAI_DATABASE_URL", "postgresql://y")
    assert srv._resolve_database_url() == "postgresql://y"   # SAGEWAI_* takes precedence


def test_nested_config_secret_is_encrypted_and_redacted(tmp_path):
    from sagewai.admin.state_file import AdminStateFile
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.upsert_provider({"provider_name": "x", "config": {"nested": {"api_key": "sk-NESTED"}}})
    raw = (tmp_path / "s.json").read_text()
    assert "sk-NESTED" not in raw and "fernet:" in raw
    listed = sf.list_providers()
    assert "sk-NESTED" not in str(listed) and "fernet:" not in str(listed)
    assert listed[0]["config"]["nested"]["api_key_set"] is True
    assert sf.get_provider_decrypted("x")["config"]["nested"]["api_key"] == "sk-NESTED"


def test_container_startup_fails_closed_with_encrypted_secret_no_key(tmp_path, monkeypatch):
    import json
    from cryptography.fernet import Fernet
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.admin.secret_crypto import AdminKeyMissing
    from sagewai.admin import serve as srv
    # seed an ENCRYPTED provider using a key, then remove the key + go container
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    p = tmp_path / "s.json"
    sf = AdminStateFile(path=p)
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "sk-XYZ"}})
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setenv("SAGEWAI_RUNTIME", "container")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path / "nohome"))   # empty home → no key resolves
    with pytest.raises(AdminKeyMissing):
        srv.create_admin_serve_app(AdminStateFile(path=p))
