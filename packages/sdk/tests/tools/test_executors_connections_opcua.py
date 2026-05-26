# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog connections executor — OPC UA dispatch tests + decrypt path."""
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
        allowed_protocols=("opcua",),
    )


@pytest.fixture
def router(monkeypatch):
    """Router with a per-test Fernet key for the local backend."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    return CredentialsBackendRouter(default_backend="local")


def _make_conn(store, *, protocol_data=None):
    return store.create(
        protocol="opcua",
        project_id="proj-test",
        display_name="plant-floor",
        tags=["industrial"],
        credentials_backend={"kind": "local"},
        protocol_data=protocol_data or {
            "endpoint_url": "opc.tcp://server.example.com:4840",
            "security_mode": "None",
            "security_policy": "None",
            "auth_mode": "username",
            "username": "svc_account",
            "password": "plaintext-password",  # plaintext for the simple dispatch test
            "operations": [
                {"name": "read_temperature", "kind": "read", "node_id": "ns=2;s=Temp"},
            ],
        },
    )


@pytest.mark.asyncio
async def test_opcua_kind_dispatches_to_opcua_run_op(store, router):
    _make_conn(store)

    payload = {
        "_kind": "opcua",
        "exec": {
            "opcua": {
                "connection_ref": "plant-floor",
                "operation": "read_temperature",
                "args": {},
            }
        },
        "project_id": "proj-test",
    }

    fake_result = {
        "value": 22.5,
        "status_code": "Good",
        "source_timestamp": "2026-05-26T12:00:00+00:00",
        "server_timestamp": "2026-05-26T12:00:00.001+00:00",
    }

    with patch(
        "sagewai.tools.executors.connections._opcua_run_op",
        AsyncMock(return_value=fake_result),
    ) as mock_run:
        result = await connections_run(payload, store=store, router=router)

    assert result == fake_result
    assert mock_run.await_args.kwargs["op"] == "read_temperature"


@pytest.mark.asyncio
async def test_opcua_password_decrypted_before_runner(store, router):
    """OPC UA's `password` field is sensitive. The executor must decrypt
    the encrypted ciphertext before invoking _run_op, so the runner sees
    plaintext only."""
    # Encrypt the password the way the admin POST / route does, then write
    # the encrypted record into the store.
    plaintext_pd = {
        "endpoint_url": "opc.tcp://server.example.com:4840",
        "security_mode": "None",
        "security_policy": "None",
        "auth_mode": "username",
        "username": "svc_account",
        "password": "my-real-password",
        "operations": [
            {"name": "read_temperature", "kind": "read", "node_id": "ns=2;s=Temp"},
        ],
        "sandbox_tier_override": None,
    }
    encrypted_pd = router.encrypt(
        plaintext_pd,
        sensitive_field_paths=("password",),
        connection_credentials_backend={"kind": "local"},
    )
    # Sanity: password must now be ciphertext (fernet: prefix).
    assert encrypted_pd["password"].startswith("fernet:"), (
        "test setup invariant: encrypt_fields should produce fernet:-prefixed ciphertext"
    )

    store.create(
        protocol="opcua",
        project_id="proj-test",
        display_name="plant-floor",
        tags=["industrial"],
        credentials_backend={"kind": "local"},
        protocol_data=encrypted_pd,
    )

    # On-disk inspection: confirm the stored record carries the ciphertext.
    persisted = store.list(project_id="proj-test", protocol="opcua")[0]
    assert persisted.protocol_data["password"].startswith("fernet:")

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["password"] = connection.protocol_data["password"]
        captured["username"] = connection.protocol_data["username"]
        return {
            "value": 1,
            "status_code": "Good",
            "source_timestamp": "",
            "server_timestamp": "",
        }

    payload = {
        "_kind": "opcua",
        "exec": {
            "opcua": {
                "connection_ref": "plant-floor",
                "operation": "read_temperature",
                "args": {},
            }
        },
        "project_id": "proj-test",
    }

    with patch(
        "sagewai.tools.executors.connections._opcua_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    # The runner MUST receive the plaintext password, not the ciphertext.
    assert captured["password"] == "my-real-password", (
        f"runner received {captured['password']!r}, but expected plaintext "
        "'my-real-password' (decryption gap on dispatch path)"
    )
    # Non-sensitive fields pass through unchanged.
    assert captured["username"] == "svc_account"


@pytest.mark.asyncio
async def test_opcua_missing_connection_raises(store, router):
    payload = {
        "_kind": "opcua",
        "exec": {
            "opcua": {
                "connection_ref": "nonexistent",
                "operation": "read_temperature",
                "args": {},
            }
        },
        "project_id": "proj-test",
    }
    with pytest.raises(ValueError, match="'nonexistent' not found"):
        await connections_run(payload, store=store, router=router)
