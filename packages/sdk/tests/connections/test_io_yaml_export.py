# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai.connections.io_yaml.export_to_yaml."""
from __future__ import annotations

import os

import pytest
import yaml as pyyaml
from cryptography.fernet import Fernet

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.io_yaml import export_to_yaml
from sagewai.connections.store import ConnectionStore


@pytest.fixture
def master_key():
    """Set SAGEWAI_MASTER_KEY for tests that exercise the Fernet backend."""
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


# ── basic shape ───────────────────────────────────────────────────────


def test_empty_project_exports_empty_connections_list(store, router):
    yaml_str = export_to_yaml(store=store, router=router, project_id="empty-proj")
    body = pyyaml.safe_load(yaml_str)
    assert body["version"] == 1
    assert body["secrets_mode"] == "redacted"
    assert body["connections"] == []


def test_export_contains_required_top_level_fields(store, router):
    yaml_str = export_to_yaml(store=store, router=router, project_id="x")
    body = pyyaml.safe_load(yaml_str)
    assert "version" in body
    assert "project" in body
    assert "exported_at" in body
    assert "exported_by_version" in body
    assert "secrets_mode" in body
    assert "connections" in body


def test_export_single_http_connection(store, router):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="hubspot",
        tags=["crm"],
        protocol_data={"base_url": "https://api.hubspot.com", "auth": {"kind": "bearer"}},
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(store=store, router=router, project_id="proj")
    body = pyyaml.safe_load(yaml_str)
    assert len(body["connections"]) == 1
    conn = body["connections"][0]
    assert conn["protocol"] == "http"
    assert conn["display_name"] == "hubspot"
    assert conn["tags"] == ["crm"]
    assert conn["credentials_backend"]["kind"] == "local"
    assert conn["protocol_data"]["base_url"] == "https://api.hubspot.com"


# ── secrets modes ─────────────────────────────────────────────────────


def test_redacted_mode_nulls_sensitive_fields(store, router):
    """In redacted mode, password and other sensitive fields → null."""
    # OAuth2 has client_secret + tokens.access_token as sensitive paths.
    store.create(
        protocol="oauth2",
        project_id="proj",
        display_name="spotify",
        tags=[],
        protocol_data={
            "provider": "spotify",
            "client_id": "id",
            "client_secret": "secret",
            "scopes": [],
            "tokens": None,
        },
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", secrets_mode="redacted"
    )
    body = pyyaml.safe_load(yaml_str)
    pd = body["connections"][0]["protocol_data"]
    assert pd["client_id"] == "id"  # non-sensitive
    assert pd["client_secret"] is None  # redacted


def test_encrypted_mode_preserves_ciphertext(store, router):
    """In encrypted mode, sensitive fields keep their fernet:-prefixed form."""
    # Manually create a row with already-encrypted client_secret.
    plaintext_pd = {
        "provider": "spotify",
        "client_id": "id",
        "client_secret": "rawsecret",
        "scopes": [],
        "tokens": None,
    }
    encrypted_pd = router.encrypt(
        plaintext_pd,
        sensitive_field_paths=("client_secret",),
        connection_credentials_backend={"kind": "local"},
    )
    assert encrypted_pd["client_secret"].startswith("fernet:")

    store.create(
        protocol="oauth2",
        project_id="proj",
        display_name="spotify",
        tags=[],
        protocol_data=encrypted_pd,
        credentials_backend={"kind": "local"},
    )

    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", secrets_mode="encrypted"
    )
    body = pyyaml.safe_load(yaml_str)
    pd = body["connections"][0]["protocol_data"]
    assert pd["client_secret"].startswith("fernet:")


def test_placeholder_mode_emits_env_var_references(store, router):
    """In placeholder mode, sensitive fields → ${UPPER_SNAKE_DISPLAY_NAME_FIELD}."""
    store.create(
        protocol="oauth2",
        project_id="proj",
        display_name="spotify-marketing",
        tags=[],
        protocol_data={
            "provider": "spotify",
            "client_id": "id",
            "client_secret": "secret",
            "scopes": [],
            "tokens": None,
        },
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", secrets_mode="placeholder"
    )
    body = pyyaml.safe_load(yaml_str)
    pd = body["connections"][0]["protocol_data"]
    assert pd["client_secret"] == "${SPOTIFY_MARKETING_CLIENT_SECRET}"


# ── filters ───────────────────────────────────────────────────────────


def _create_two_protocols(store):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="http-alpha",
        tags=["crm"],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    store.create(
        protocol="modbus",
        project_id="proj",
        display_name="modbus-alpha",
        tags=["industrial"],
        protocol_data={
            "host": "x",
            "port": 502,
            "transport": "tcp",
            "unit_id": 1,
            "default_timeout_seconds": 3.0,
        },
        credentials_backend={"kind": "local"},
    )


def test_filter_by_protocol_includes_only_matching(store, router):
    _create_two_protocols(store)
    yaml_str = export_to_yaml(
        store=store,
        router=router,
        project_id="proj",
        protocols=("http",),
    )
    body = pyyaml.safe_load(yaml_str)
    protocols = {c["protocol"] for c in body["connections"]}
    assert protocols == {"http"}


def test_filter_by_multiple_protocols_ors_within_flag(store, router):
    _create_two_protocols(store)
    yaml_str = export_to_yaml(
        store=store,
        router=router,
        project_id="proj",
        protocols=("http", "modbus"),
    )
    body = pyyaml.safe_load(yaml_str)
    protocols = {c["protocol"] for c in body["connections"]}
    assert protocols == {"http", "modbus"}


def test_filter_by_tag_includes_only_matching(store, router):
    _create_two_protocols(store)
    yaml_str = export_to_yaml(
        store=store,
        router=router,
        project_id="proj",
        tags=("crm",),
    )
    body = pyyaml.safe_load(yaml_str)
    names = {c["display_name"] for c in body["connections"]}
    assert names == {"http-alpha"}


def test_filter_combined_protocol_and_tag_ands_between_flags(store, router):
    _create_two_protocols(store)
    # http connection has tag crm; modbus has industrial
    yaml_str = export_to_yaml(
        store=store,
        router=router,
        project_id="proj",
        protocols=("http",),
        tags=("industrial",),  # http connection doesn't have industrial → empty
    )
    body = pyyaml.safe_load(yaml_str)
    assert body["connections"] == []


# ── id handling ───────────────────────────────────────────────────────


def test_export_omits_id_by_default(store, router):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(store=store, router=router, project_id="proj")
    body = pyyaml.safe_load(yaml_str)
    assert "id" not in body["connections"][0]


def test_export_include_id_emits_id(store, router):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", include_id=True
    )
    body = pyyaml.safe_load(yaml_str)
    assert "id" in body["connections"][0]
    assert body["connections"][0]["id"]  # non-empty


# ── operational fields stripped ──────────────────────────────────────


def test_export_does_not_include_status_or_timestamps(store, router):
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=[],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(store=store, router=router, project_id="proj")
    body = pyyaml.safe_load(yaml_str)
    conn = body["connections"][0]
    for field in (
        "status",
        "last_tested_at",
        "last_test_ok",
        "last_error",
        "created_at",
        "updated_at",
    ):
        assert field not in conn, f"unexpected operational field {field} in export"


# ── output format ────────────────────────────────────────────────────


def test_export_yaml_is_valid_yaml(store, router):
    """Output must round-trip through safe_load + safe_dump cleanly."""
    yaml_str = export_to_yaml(store=store, router=router, project_id="empty")
    parsed = pyyaml.safe_load(yaml_str)
    redumped = pyyaml.safe_dump(parsed, sort_keys=False)
    assert pyyaml.safe_load(redumped) == parsed


def test_export_yaml_block_style_not_flow_style(store, router):
    """Output should be block-style (operator-readable), not flow-style."""
    store.create(
        protocol="http",
        project_id="proj",
        display_name="alpha",
        tags=["a", "b"],
        protocol_data={"base_url": "https://a.com", "auth": {"kind": "none"}},
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(store=store, router=router, project_id="proj")
    # Block style: list items prefixed with "- "
    assert "- protocol:" in yaml_str


# ── invalid mode ─────────────────────────────────────────────────────


def test_invalid_secrets_mode_raises(store, router):
    with pytest.raises(ValueError, match="secrets_mode"):
        export_to_yaml(
            store=store, router=router, project_id="x", secrets_mode="bogus"
        )


# ── encrypted-mode plaintext leak guard ──────────────────────────────


def test_encrypted_mode_redacts_plaintext_sensitive_field(store, router, caplog):
    """If a sensitive field is in plaintext (not a storage-form marker) when
    secrets_mode=encrypted, the exporter MUST redact it to null rather than
    leak the plaintext through. Defense-in-depth against mixed-state records
    (upstream bug, kind:none backend, partially-encrypted row, etc.)."""
    # Create an OAuth2 connection but bypass the normal encryption path:
    # write client_secret as plaintext directly. (The router would have
    # produced a fernet:-prefixed string, but mixed-state records exist
    # in the wild — this is the failure mode the guard protects against.)
    store.create(
        protocol="oauth2",
        project_id="proj",
        display_name="spotify",
        tags=[],
        protocol_data={
            "provider": "spotify",
            "client_id": "id",
            "client_secret": "plaintext-leak",  # NOT in storage form
            "redirect_uri": "http://localhost/callback",
            "requested_scopes": ["read"],
            "granted_scopes": [],
            "tokens": None,
        },
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", secrets_mode="encrypted"
    )
    body = pyyaml.safe_load(yaml_str)
    pd = body["connections"][0]["protocol_data"]
    assert pd["client_secret"] is None, (
        f"plaintext leaked into encrypted-mode export: {pd['client_secret']!r}"
    )


def test_encrypted_mode_preserves_storage_form_marker(store, router):
    """In encrypted mode, a properly-encrypted (storage-form) field keeps
    its marker; only plaintext values get redacted to None."""
    plaintext_pd = {
        "provider": "spotify",
        "client_id": "id",
        "client_secret": "rawsecret",
        "redirect_uri": "http://localhost/callback",
        "requested_scopes": ["read"],
        "granted_scopes": [],
        "tokens": None,
    }
    encrypted_pd = router.encrypt(
        plaintext_pd,
        sensitive_field_paths=("client_secret",),
        connection_credentials_backend={"kind": "local"},
    )
    assert encrypted_pd["client_secret"].startswith("fernet:")
    store.create(
        protocol="oauth2",
        project_id="proj",
        display_name="spotify",
        tags=[],
        protocol_data=encrypted_pd,
        credentials_backend={"kind": "local"},
    )
    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", secrets_mode="encrypted"
    )
    body = pyyaml.safe_load(yaml_str)
    pd = body["connections"][0]["protocol_data"]
    assert pd["client_secret"].startswith("fernet:")


def test_encrypted_mode_preserves_env_marker_dict(store, router):
    """In encrypted mode, a dict-form storage marker ({"$env": ...}) is
    also preserved end-to-end."""
    store.create(
        protocol="oauth2",
        project_id="proj",
        display_name="spotify",
        tags=[],
        protocol_data={
            "provider": "spotify",
            "client_id": "id",
            "client_secret": {"$env": "SPOTIFY_CLIENT_SECRET"},
            "redirect_uri": "http://localhost/callback",
            "requested_scopes": ["read"],
            "granted_scopes": [],
            "tokens": None,
        },
        credentials_backend={"kind": "env"},
    )
    yaml_str = export_to_yaml(
        store=store, router=router, project_id="proj", secrets_mode="encrypted"
    )
    body = pyyaml.safe_load(yaml_str)
    pd = body["connections"][0]["protocol_data"]
    assert pd["client_secret"] == {"$env": "SPOTIFY_CLIENT_SECRET"}
