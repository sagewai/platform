# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog connections executor — WebSocket dispatch + decrypt path tests."""
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
        allowed_protocols=("websocket",),
    )


@pytest.fixture
def router(monkeypatch):
    """Router with a per-test Fernet key for the local backend."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    return CredentialsBackendRouter(default_backend="local")


def _make_conn(store, *, protocol_data=None):
    return store.create(
        protocol="websocket",
        project_id="proj-test",
        display_name="market-data",
        tags=["finance"],
        credentials_backend={"kind": "local"},
        protocol_data=protocol_data or {
            "url": "wss://gateway.example.com/ws",
            "headers": {},
            "auth_header_name": "Authorization",
            "auth_header_value": "plaintext-token",
            "default_timeout_seconds": 30.0,
            "operations": [
                {"name": "get_quote", "message_template": '{"symbol": "{symbol}"}'},
            ],
            "sandbox_tier_override": None,
        },
    )


@pytest.mark.asyncio
async def test_websocket_kind_dispatches_to_websocket_run_op(store, router):
    _make_conn(store)

    payload = {
        "_kind": "websocket",
        "exec": {
            "websocket": {
                "connection_ref": "market-data",
                "operation": "get_quote",
                "args": {"symbol": "AAPL"},
            }
        },
        "project_id": "proj-test",
    }

    fake_result = {"frame": '{"price": 42.5}'}

    with patch(
        "sagewai.tools.executors.connections._websocket_run_op",
        AsyncMock(return_value=fake_result),
    ) as mock_run:
        result = await connections_run(payload, store=store, router=router)

    assert result == fake_result
    assert mock_run.await_args.kwargs["op"] == "get_quote"


@pytest.mark.asyncio
async def test_websocket_auth_header_value_decrypted_before_runner(store, router):
    """auth_header_value is sensitive. The shared executor must decrypt
    the encrypted ciphertext before invoking _run_op, so the runner sees
    plaintext only."""
    plaintext_pd = {
        "url": "wss://gateway.example.com/ws",
        "headers": {},
        "auth_header_name": "Authorization",
        "auth_header_value": "Bearer real-secret-token",
        "default_timeout_seconds": 30.0,
        "operations": [
            {"name": "get_quote", "message_template": '{"symbol": "{symbol}"}'},
        ],
        "sandbox_tier_override": None,
    }
    encrypted_pd = router.encrypt(
        plaintext_pd,
        sensitive_field_paths=("auth_header_value",),
        connection_credentials_backend={"kind": "local"},
    )
    assert encrypted_pd["auth_header_value"].startswith("fernet:"), (
        "test setup invariant: encrypt_fields should produce fernet:-prefixed ciphertext"
    )

    store.create(
        protocol="websocket",
        project_id="proj-test",
        display_name="market-data",
        tags=["finance"],
        credentials_backend={"kind": "local"},
        protocol_data=encrypted_pd,
    )

    # Sanity: on-disk record is encrypted.
    persisted = store.list(project_id="proj-test", protocol="websocket")[0]
    assert persisted.protocol_data["auth_header_value"].startswith("fernet:")

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["auth_header_value"] = connection.protocol_data["auth_header_value"]
        captured["url"] = connection.protocol_data["url"]
        return {"frame": "{}"}

    payload = {
        "_kind": "websocket",
        "exec": {
            "websocket": {
                "connection_ref": "market-data",
                "operation": "get_quote",
                "args": {"symbol": "AAPL"},
            }
        },
        "project_id": "proj-test",
    }

    with patch(
        "sagewai.tools.executors.connections._websocket_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    # The runner MUST receive the plaintext value, not the ciphertext.
    assert captured["auth_header_value"] == "Bearer real-secret-token", (
        f"runner received {captured['auth_header_value']!r}, but expected plaintext "
        "'Bearer real-secret-token' (decryption gap on dispatch path)"
    )
    # Non-sensitive fields pass through unchanged.
    assert captured["url"] == "wss://gateway.example.com/ws"


@pytest.mark.asyncio
async def test_websocket_missing_connection_raises(store, router):
    payload = {
        "_kind": "websocket",
        "exec": {
            "websocket": {
                "connection_ref": "nonexistent",
                "operation": "get_quote",
                "args": {},
            }
        },
        "project_id": "proj-test",
    }
    with pytest.raises(ValueError, match="'nonexistent' not found"):
        await connections_run(payload, store=store, router=router)
