# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.connections.io_yaml.import_from_yaml."""
from __future__ import annotations

import os
from textwrap import dedent

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.io_yaml import import_from_yaml
from sagewai.connections.store import ConnectionStore


@pytest.fixture
def master_key():
    saved = os.environ.get("SAGEWAI_MASTER_KEY")
    os.environ["SAGEWAI_MASTER_KEY"] = Fernet.generate_key().decode()
    yield
    if saved is None:
        os.environ.pop("SAGEWAI_MASTER_KEY", None)
    else:
        os.environ["SAGEWAI_MASTER_KEY"] = saved


@pytest.fixture
def router(master_key):
    return CredentialsBackendRouter(default_backend="local")


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(
        tmp_path / "connections.json",
        allowed_protocols=("http", "oauth2", "modbus"),
    )


_BASIC_YAML = dedent("""
    version: 1
    project:
      id: proj
      display_name: proj
    exported_at: '2026-05-26T12:00:00+00:00'
    exported_by_version: 1.0.0
    secrets_mode: redacted
    connections:
      - protocol: http
        display_name: alpha
        tags: [crm]
        credentials_backend:
          kind: local
        is_default: true
        protocol_data:
          base_url: https://a.com
          auth:
            kind: none
""").strip()


# ── happy path ────────────────────────────────────────────────────────


def test_import_create_only_creates_rows(store, router):
    result = import_from_yaml(
        yaml_text=_BASIC_YAML,
        store=store,
        router=router,
        project_id="proj",
        mode="create-only",
    )
    assert result["errors"] == []
    assert len(result["created"]) == 1
    assert result["created"][0]["display_name"] == "alpha"
    assert result["created"][0]["protocol"] == "http"
    assert result["updated"] == []
    assert result["skipped"] == []

    # Verify persistence
    conns = list(store.list(project_id="proj"))
    assert len(conns) == 1
    assert conns[0].display_name == "alpha"
    assert conns[0].protocol_data["base_url"] == "https://a.com"


def test_import_dry_run_does_not_persist(store, router):
    result = import_from_yaml(
        yaml_text=_BASIC_YAML,
        store=store,
        router=router,
        project_id="proj",
        mode="create-only",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert len(result["created"]) == 1
    # No persistence.
    conns = list(store.list(project_id="proj"))
    assert conns == []


# ── parse + version errors ───────────────────────────────────────────


def test_import_invalid_yaml_returns_parse_error(store, router):
    result = import_from_yaml(
        yaml_text="not: valid: yaml: at: all: : :",
        store=store,
        router=router,
        project_id="proj",
    )
    assert len(result["errors"]) == 1
    assert result["errors"][0]["code"] == "import_yaml_parse_error"


def test_import_missing_version_returns_version_error(store, router):
    result = import_from_yaml(
        yaml_text="connections: []",
        store=store,
        router=router,
        project_id="proj",
    )
    assert result["errors"][0]["code"] == "import_unknown_version"


def test_import_unsupported_version_returns_version_error(store, router):
    result = import_from_yaml(
        yaml_text="version: 99\nconnections: []",
        store=store,
        router=router,
        project_id="proj",
    )
    assert result["errors"][0]["code"] == "import_unknown_version"


# ── unknown protocol / backend ────────────────────────────────────────


def test_import_unknown_protocol_returns_error(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: quantum
            display_name: q
            tags: []
            credentials_backend:
              kind: local
            is_default: false
            protocol_data: {}
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text, store=store, router=router, project_id="proj"
    )
    assert any(e["code"] == "import_unknown_protocol" for e in result["errors"])


def test_import_unknown_backend_returns_error(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: bogus_backend
            is_default: false
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text, store=store, router=router, project_id="proj"
    )
    assert any(e["code"] == "import_unknown_backend" for e in result["errors"])


def test_import_invalid_protocol_data_returns_error(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: false
            protocol_data:
              # missing base_url + auth — http schema requires both
              unknown_field: true
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text, store=store, router=router, project_id="proj"
    )
    assert any(e["code"] == "import_protocol_data_invalid" for e in result["errors"])


# ── collision modes ──────────────────────────────────────────────────


def test_import_create_only_collision_aborts_all_writes(store, router):
    # Seed an existing row
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://existing.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    result = import_from_yaml(
        yaml_text=_BASIC_YAML,
        store=store,
        router=router,
        project_id="proj",
        mode="create-only",
    )
    # Must fail and not have written anything new
    assert any(
        e["code"] == "import_display_name_collision" for e in result["errors"]
    )
    conns = list(store.list(project_id="proj"))
    assert len(conns) == 1
    assert conns[0].protocol_data["base_url"] == "https://existing.com"


def test_import_upsert_replaces_existing(store, router):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://existing.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    result = import_from_yaml(
        yaml_text=_BASIC_YAML,
        store=store,
        router=router,
        project_id="proj",
        mode="upsert",
    )
    assert result["errors"] == [], result["errors"]
    assert len(result["updated"]) == 1
    assert result["updated"][0]["display_name"] == "alpha"
    conns = list(store.list(project_id="proj"))
    assert len(conns) == 1
    assert conns[0].protocol_data["base_url"] == "https://a.com"


def test_import_skip_existing_skips(store, router):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://existing.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    result = import_from_yaml(
        yaml_text=_BASIC_YAML,
        store=store,
        router=router,
        project_id="proj",
        mode="skip-existing",
    )
    assert result["errors"] == [], result["errors"]
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["display_name"] == "alpha"
    conns = list(store.list(project_id="proj"))
    assert conns[0].protocol_data["base_url"] == "https://existing.com"


# ── placeholder mode ─────────────────────────────────────────────────


def test_import_placeholder_resolves_env_var(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: placeholder
        connections:
          - protocol: oauth2
            display_name: spotify
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              provider: spotify
              client_id: id
              client_secret: ${SPOTIFY_CLIENT_SECRET}
              redirect_uri: http://localhost/callback
              requested_scopes: [user-read-email]
              granted_scopes: []
              tokens: null
    """).strip()
    saved = os.environ.get("SPOTIFY_CLIENT_SECRET")
    os.environ["SPOTIFY_CLIENT_SECRET"] = "supersecret"
    try:
        result = import_from_yaml(
            yaml_text=yaml_text, store=store, router=router, project_id="proj"
        )
        assert result["errors"] == [], result["errors"]
        assert len(result["created"]) == 1
        conns = list(store.list(project_id="proj"))
        # The plaintext should be in the persisted protocol_data
        assert conns[0].protocol_data["client_secret"] == "supersecret"
    finally:
        if saved is None:
            os.environ.pop("SPOTIFY_CLIENT_SECRET", None)
        else:
            os.environ["SPOTIFY_CLIENT_SECRET"] = saved


def test_import_placeholder_missing_env_var_errors(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: placeholder
        connections:
          - protocol: oauth2
            display_name: spotify
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              provider: spotify
              client_id: id
              client_secret: ${NEVER_SET_VAR}
              scopes: []
              tokens: null
    """).strip()
    os.environ.pop("NEVER_SET_VAR", None)
    result = import_from_yaml(
        yaml_text=yaml_text, store=store, router=router, project_id="proj"
    )
    assert any(e["code"] == "import_env_var_missing" for e in result["errors"])


# ── preserve_ids ─────────────────────────────────────────────────────


def test_import_preserve_ids_honors_yaml_id(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - id: conn-imported-001
            protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        preserve_ids=True,
    )
    assert result["errors"] == [], result["errors"]
    assert result["created"][0]["id"] == "conn-imported-001"


def test_import_preserve_ids_without_id_in_yaml_errors(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        preserve_ids=True,
    )
    # Must report per-row error
    assert any(
        e["code"] == "import_id_collision" or "preserve" in e["message"].lower()
        for e in result["errors"]
    )


def test_import_preserve_ids_collision_errors(store, router):
    # Seed a connection with a known id
    store.create(
        protocol="http",
        project_id="proj",
        display_name="existing",
        tags=[],
        protocol_data={"base_url": "https://x.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
        id_override="conn-collision-id",
    )
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - id: conn-collision-id
            protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        mode="create-only",
        preserve_ids=True,
    )
    assert any(e["code"] == "import_id_collision" for e in result["errors"])


# ── is_default collision ────────────────────────────────────────────


def test_import_default_collision_within_batch_errors(store, router):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
          - protocol: http
            display_name: beta
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://b.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text, store=store, router=router, project_id="proj"
    )
    assert any(e["code"] == "import_default_collision" for e in result["errors"])


def test_import_upsert_default_collision_does_not_silently_promote(store, router):
    """In upsert mode, two rows both is_default:true for the same protocol must
    surface import_default_collision AND must NOT promote either row to
    default (last-write-wins would otherwise silently demote the existing
    default and promote whichever row landed last)."""
    # Seed two existing connections; first one is auto-default.
    first = store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    store.create(
        protocol="http",
        project_id="proj",
        display_name="beta",
        tags=[],
        protocol_data={"base_url": "https://b.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    assert first.is_default is True

    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
          - protocol: http
            display_name: beta
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://b.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        mode="upsert",
    )

    # Error reported.
    assert any(e["code"] == "import_default_collision" for e in result["errors"])
    # Original default flag preserved on alpha (no silent flip to beta).
    conns = {c.display_name: c for c in store.list(project_id="proj")}
    assert conns["alpha"].is_default is True, (
        "upsert silently demoted the original default after a batch collision"
    )
    assert conns["beta"].is_default is False, (
        "upsert silently promoted a row whose is_default flag collided with another"
    )


def test_import_skip_existing_default_collision_does_not_promote(store, router):
    """In skip-existing mode, a default-flag collision must also block both
    rows from being promoted to default. Existing connections persist
    unchanged (skip-existing semantics) AND the default flag is untouched."""
    first = store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    store.create(
        protocol="http",
        project_id="proj",
        display_name="beta",
        tags=[],
        protocol_data={"base_url": "https://b.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    assert first.is_default is True

    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
          - protocol: http
            display_name: beta
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://b.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        mode="skip-existing",
    )
    # Error reported.
    assert any(e["code"] == "import_default_collision" for e in result["errors"])
    # Both rows were skipped (display_name matches existing), AND the
    # default flag wasn't flipped.
    conns = {c.display_name: c for c in store.list(project_id="proj")}
    assert conns["alpha"].is_default is True
    assert conns["beta"].is_default is False


# ── operational fields ignored ──────────────────────────────────────


def test_import_ignores_status_and_timestamps_in_yaml(store, router):
    """If a malicious or stale YAML includes status/timestamps, the importer ignores them."""
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            status: ready
            last_tested_at: '2020-01-01T00:00:00+00:00'
            last_test_ok: true
            created_at: '2020-01-01T00:00:00+00:00'
            updated_at: '2020-01-01T00:00:00+00:00'
            last_error: stale error
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()
    result = import_from_yaml(
        yaml_text=yaml_text, store=store, router=router, project_id="proj"
    )
    assert result["errors"] == [], result["errors"]
    conns = list(store.list(project_id="proj"))
    assert conns[0].status == "pending"  # not "ready"
    assert conns[0].last_tested_at is None
    assert conns[0].last_error is None


# ── is_default propagation ───────────────────────────────────────────


def test_import_upsert_promotes_imported_default(store, router):
    """upsert mode: an imported is_default:true should replace the existing default."""
    # Seed two existing http connections; first one will be auto-default.
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    store.create(
        protocol="http",
        project_id="proj",
        display_name="beta",
        tags=[],
        protocol_data={"base_url": "https://b.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    conns_pre = {c.display_name: c for c in store.list(project_id="proj")}
    assert conns_pre["alpha"].is_default is True
    assert conns_pre["beta"].is_default is False

    # Import a YAML that flags beta as default.
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: beta
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://b.com
              auth:
                kind: none
    """).strip()

    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        mode="upsert",
    )

    assert result["errors"] == [], result["errors"]
    assert len(result["updated"]) == 1

    # Now beta should be the default, alpha should not be.
    conns_post = {c.display_name: c for c in store.list(project_id="proj")}
    assert conns_post["beta"].is_default is True
    assert conns_post["alpha"].is_default is False


def test_import_create_promotes_imported_default(store, router):
    """create-only mode: an imported is_default:true row should be marked default,
    even when an existing connection in the same group was previously default."""
    # Seed an existing default http connection.
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )

    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: beta
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://b.com
              auth:
                kind: none
    """).strip()

    result = import_from_yaml(
        yaml_text=yaml_text,
        store=store,
        router=router,
        project_id="proj",
        mode="create-only",
    )

    assert result["errors"] == [], result["errors"]
    conns_post = {c.display_name: c for c in store.list(project_id="proj")}
    assert conns_post["beta"].is_default is True
    assert conns_post["alpha"].is_default is False


# ── encrypted mode / master-key mismatch ─────────────────────────────


def test_import_encrypted_mode_with_wrong_master_key_errors(store, tmp_path):
    """encrypted-mode YAML against a target with a different master key
    must surface import_master_key_mismatch, not silently import."""
    # Build encrypted ciphertext under master key A.
    saved = os.environ.get("SAGEWAI_MASTER_KEY")
    os.environ["SAGEWAI_MASTER_KEY"] = Fernet.generate_key().decode()
    try:
        router_a = CredentialsBackendRouter(default_backend="local")
        encrypted_pd = router_a.encrypt(
            {
                "provider": "spotify",
                "client_id": "id",
                "client_secret": "rawvalue",
                "redirect_uri": "http://localhost/callback",
                "requested_scopes": ["user-read-email"],
                "granted_scopes": [],
                "tokens": None,
            },
            sensitive_field_paths=("client_secret",),
            connection_credentials_backend={"kind": "local"},
        )
        assert encrypted_pd["client_secret"].startswith("fernet:")
        encrypted_secret = encrypted_pd["client_secret"]

        # Switch master key (simulates DR into a fresh env with no shared key).
        os.environ["SAGEWAI_MASTER_KEY"] = Fernet.generate_key().decode()
        router_b = CredentialsBackendRouter(default_backend="local")

        yaml_text = dedent(f"""
            version: 1
            secrets_mode: encrypted
            connections:
              - protocol: oauth2
                display_name: spotify
                tags: []
                credentials_backend:
                  kind: local
                is_default: true
                protocol_data:
                  provider: spotify
                  client_id: id
                  client_secret: {encrypted_secret}
                  redirect_uri: http://localhost/callback
                  requested_scopes: [user-read-email]
                  granted_scopes: []
                  tokens: null
        """).strip()

        result = import_from_yaml(
            yaml_text=yaml_text,
            store=store,
            router=router_b,
            project_id="proj",
        )
        assert any(
            e["code"] == "import_master_key_mismatch" for e in result["errors"]
        ), result["errors"]
        # No connection should be persisted.
        assert list(store.list(project_id="proj")) == []
    finally:
        if saved is None:
            os.environ.pop("SAGEWAI_MASTER_KEY", None)
        else:
            os.environ["SAGEWAI_MASTER_KEY"] = saved


# ── round trip ───────────────────────────────────────────────────────


def test_import_round_trip(store, router, tmp_path):
    """Export from one store, import into another, verify round-trip."""
    from sagewai.connections.io_yaml import export_to_yaml

    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=["crm", "marketing"],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )

    yaml_str = export_to_yaml(store=store, router=router, project_id="proj")

    # Fresh target store
    target = ConnectionStore(
        tmp_path / "target.json",
        allowed_protocols=("http",),
    )
    result = import_from_yaml(
        yaml_text=yaml_str, store=target, router=router, project_id="proj"
    )

    assert result["errors"] == [], result["errors"]
    assert len(result["created"]) == 1

    target_conns = list(target.list(project_id="proj"))
    assert len(target_conns) == 1
    assert target_conns[0].display_name == "alpha"
    assert target_conns[0].tags == ("crm", "marketing")
    assert target_conns[0].protocol_data["base_url"] == "https://a.com"
