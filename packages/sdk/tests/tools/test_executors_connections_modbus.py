# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog connections executor — Modbus dispatch tests."""
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
        allowed_protocols=("modbus",),
    )


@pytest.fixture
def router(monkeypatch):
    """Router with a per-test Fernet key for the local backend."""
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    return CredentialsBackendRouter(default_backend="local")


def _make_conn(store):
    return store.create(
        protocol="modbus",
        project_id="proj-test",
        display_name="industrial-pump",
        tags=["industrial"],
        credentials_backend={"kind": "local"},
        protocol_data={
            "host": "192.168.1.50",
            "port": 502,
            "transport": "tcp",
            "unit_id": 1,
            "default_timeout_seconds": 3.0,
        },
    )


@pytest.mark.asyncio
async def test_modbus_kind_dispatches_to_modbus_run_op(store, router):
    _make_conn(store)

    payload = {
        "_kind": "modbus",
        "exec": {
            "modbus": {
                "connection_ref": "industrial-pump",
                "operation": "read_holding_registers",
                "args": {"address": 0, "count": 1},
            }
        },
        "project_id": "proj-test",
    }

    fake_result = [42]

    with patch(
        "sagewai.tools.executors.connections._modbus_run_op",
        AsyncMock(return_value=fake_result),
    ) as mock_run:
        result = await connections_run(payload, store=store, router=router)

    assert result == fake_result
    assert mock_run.await_args.kwargs["op"] == "read_holding_registers"
    assert mock_run.await_args.kwargs["args"] == {"address": 0, "count": 1}


@pytest.mark.asyncio
async def test_modbus_kwargs_merge_into_args(store, router):
    """Agent-side tool call passes kwargs; executor merges them into args dict."""
    _make_conn(store)

    payload = {
        "_kind": "modbus",
        "exec": {
            "modbus": {
                "connection_ref": "industrial-pump",
                "operation": "write_single_register",
                "args": {"address": 100},  # baseline
            }
        },
        "project_id": "proj-test",
        "value": 4242,  # caller-side kwarg
    }

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["args"] = dict(args)
        return {"ok": True}

    with patch(
        "sagewai.tools.executors.connections._modbus_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    assert captured["args"]["address"] == 100
    assert captured["args"]["value"] == 4242


@pytest.mark.asyncio
async def test_modbus_missing_connection_raises(store, router):
    payload = {
        "_kind": "modbus",
        "exec": {
            "modbus": {
                "connection_ref": "nonexistent",
                "operation": "read_coils",
                "args": {"address": 0, "count": 1},
            }
        },
        "project_id": "proj-test",
    }
    with pytest.raises(ValueError, match="'nonexistent' not found"):
        await connections_run(payload, store=store, router=router)


@pytest.mark.asyncio
async def test_modbus_no_sensitive_fields_decrypt_is_noop(store, router):
    """Modbus has sensitive_fields=(). Executor decrypt should pass through clean."""
    _make_conn(store)
    # protocol_data should survive the round trip unchanged because Modbus has
    # no sensitive_fields — the executor short-circuits the decrypt call.

    payload = {
        "_kind": "modbus",
        "exec": {
            "modbus": {
                "connection_ref": "industrial-pump",
                "operation": "read_coils",
                "args": {"address": 0, "count": 1},
            }
        },
        "project_id": "proj-test",
    }

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["protocol_data"] = dict(connection.protocol_data)
        return [True]

    with patch(
        "sagewai.tools.executors.connections._modbus_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    # All fields plaintext.
    assert captured["protocol_data"]["host"] == "192.168.1.50"
    assert captured["protocol_data"]["unit_id"] == 1
