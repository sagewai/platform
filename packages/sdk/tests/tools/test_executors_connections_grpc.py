# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog connections executor — gRPC dispatch + decrypt path tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.store import ConnectionStore
from sagewai.tools.executors.connections import run as connections_run


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(
        store_path=tmp_path / "connections.json",
        allowed_protocols=("grpc",),
    )


@pytest.fixture
def router(monkeypatch):
    """Router with a per-test Fernet key for the local backend."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    return CredentialsBackendRouter(default_backend="local")


def _make_conn(store, *, protocol_data=None):
    return store.create(
        protocol="grpc",
        project_id="proj-test",
        display_name="inventory-service",
        tags=["catalog"],
        credentials_backend={"kind": "local"},
        protocol_data=protocol_data
        or {
            "target": "inventory.internal:443",
            "tls": "tls",
            "tls_ca_cert": "",
            "auth_mode": "none",
            "auth_metadata_key": "authorization",
            "auth_token": "",
            "auth_token_prefix": "Bearer ",
            "default_timeout_seconds": 30.0,
            "sandbox_tier_override": None,
        },
    )


@pytest.mark.asyncio
async def test_grpc_kind_dispatches_to_grpc_run_op(store, router):
    _make_conn(store)

    payload = {
        "_kind": "grpc",
        "exec": {
            "grpc": {
                "connection_ref": "inventory-service",
                "operation": "call",
                "args": {
                    "method": "inventory.InventoryService/GetItem",
                    "request": {"item_id": "abc123"},
                },
            }
        },
        "project_id": "proj-test",
    }

    fake_result = {"item": {"id": "abc123", "name": "Widget"}}

    with patch(
        "sagewai.tools.executors.connections._grpc_run_op",
        AsyncMock(return_value=fake_result),
    ) as mock_run:
        result = await connections_run(payload, store=store, router=router)

    assert result == fake_result
    assert mock_run.await_args.kwargs["op"] == "call"
    assert mock_run.await_args.kwargs["args"]["method"] == "inventory.InventoryService/GetItem"


@pytest.mark.asyncio
async def test_grpc_auth_token_decrypted_before_runner(store, router):
    """auth_token is sensitive. The shared executor must decrypt the
    encrypted ciphertext before invoking _run_op, so the runner sees
    plaintext only."""
    plaintext_pd = {
        "target": "inventory.internal:443",
        "tls": "tls",
        "tls_ca_cert": "",
        "auth_mode": "metadata_token",
        "auth_metadata_key": "authorization",
        "auth_token": "real-bearer-secret",
        "auth_token_prefix": "Bearer ",
        "default_timeout_seconds": 30.0,
        "sandbox_tier_override": None,
    }
    encrypted_pd = router.encrypt(
        plaintext_pd,
        sensitive_field_paths=("auth_token",),
        connection_credentials_backend={"kind": "local"},
    )
    assert encrypted_pd["auth_token"].startswith("fernet:"), (
        "test setup invariant: encrypt_fields should produce fernet:-prefixed ciphertext"
    )

    store.create(
        protocol="grpc",
        project_id="proj-test",
        display_name="inventory-service",
        tags=["catalog"],
        credentials_backend={"kind": "local"},
        protocol_data=encrypted_pd,
    )

    # Sanity: on-disk record is encrypted.
    persisted = store.list(project_id="proj-test", protocol="grpc")[0]
    assert persisted.protocol_data["auth_token"].startswith("fernet:")

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["auth_token"] = connection.protocol_data["auth_token"]
        captured["target"] = connection.protocol_data["target"]
        return {"ok": True}

    payload = {
        "_kind": "grpc",
        "exec": {
            "grpc": {
                "connection_ref": "inventory-service",
                "operation": "call",
                "args": {"method": "inventory.InventoryService/GetItem", "request": {}},
            }
        },
        "project_id": "proj-test",
    }

    with patch(
        "sagewai.tools.executors.connections._grpc_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    # The runner MUST receive the plaintext value, not the ciphertext.
    assert captured["auth_token"] == "real-bearer-secret", (
        f"runner received {captured['auth_token']!r}, but expected plaintext "
        "'real-bearer-secret' (decryption gap on dispatch path)"
    )
    # Non-sensitive fields pass through unchanged.
    assert captured["target"] == "inventory.internal:443"


@pytest.mark.asyncio
async def test_grpc_missing_connection_raises(store, router):
    payload = {
        "_kind": "grpc",
        "exec": {
            "grpc": {
                "connection_ref": "nonexistent",
                "operation": "call",
                "args": {},
            }
        },
        "project_id": "proj-test",
    }
    with pytest.raises(ValueError, match="'nonexistent' not found"):
        await connections_run(payload, store=store, router=router)
