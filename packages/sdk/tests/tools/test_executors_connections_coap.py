# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tool-catalog connections executor — CoAP dispatch tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cryptography.fernet import Fernet

from sagewai.connections.credentials import CredentialsBackendRouter
from sagewai.connections.models import Connection
from sagewai.connections.store import ConnectionStore
from sagewai.tools.executors.connections import run as connections_run


@pytest.fixture
def store(tmp_path):
    return ConnectionStore(
        store_path=tmp_path / "connections.json",
        allowed_protocols=("coap",),
    )


@pytest.fixture
def router(monkeypatch):
    """Router with a per-test Fernet key for the local backend.

    Every ``connections_run`` call decrypts via this router; tests that omit
    the kwarg fall through to bootstrap (covered separately).
    """
    monkeypatch.setenv("SAGEWAI_MASTER_KEY", Fernet.generate_key().decode())
    return CredentialsBackendRouter(default_backend="local")


def _make_conn(store: ConnectionStore) -> Connection:
    return store.create(
        protocol="coap",
        project_id="proj-test",
        display_name="test-thermostat",
        tags=["iot"],
        credentials_backend={"kind": "local"},
        protocol_data={
            "base_uri": "coap://thermostat.example.com:5683",
            "psk_identity": "",
            "psk_key": "",
            "default_timeout_seconds": 5.0,
        },
    )


@pytest.mark.asyncio
async def test_coap_kind_dispatches_to_coap_run_op(store, router):
    _make_conn(store)

    payload = {
        "_kind": "coap",
        "exec": {
            "coap": {
                "connection_ref": "test-thermostat",
                "operation": "get",
                "args": {"path": "/temperature"},
            }
        },
        "project_id": "proj-test",
    }

    fake_result = {"code": "2.05", "payload": b'{"v": 22.5}', "content_format": 50}

    with patch(
        "sagewai.tools.executors.connections._coap_run_op",
        AsyncMock(return_value=fake_result),
    ) as mock_run:
        result = await connections_run(payload, store=store, router=router)

    assert result == fake_result
    assert mock_run.await_args.kwargs["op"] == "get"
    assert mock_run.await_args.kwargs["args"] == {"path": "/temperature"}


@pytest.mark.asyncio
async def test_missing_connection_raises_value_error(store, router):
    payload = {
        "_kind": "coap",
        "exec": {
            "coap": {
                "connection_ref": "nonexistent",
                "operation": "get",
                "args": {"path": "/"},
            }
        },
        "project_id": "proj-test",
    }
    with pytest.raises(ValueError, match="'nonexistent' not found"):
        await connections_run(payload, store=store, router=router)


@pytest.mark.asyncio
async def test_unknown_kind_raises_value_error(store, router):
    payload = {
        "_kind": "websocket",  # websocket is still un-wired (PR4)
        "exec": {"websocket": {"connection_ref": "x", "operation": "send"}},
        "project_id": "proj-test",
    }
    with pytest.raises(ValueError, match="'websocket' is not yet wired"):
        await connections_run(payload, store=store, router=router)


@pytest.mark.asyncio
async def test_kwargs_payload_merges_into_args(store, router):
    """Agent-side tool call passes kwargs; executor merges them into args dict."""
    _make_conn(store)

    payload = {
        "_kind": "coap",
        "exec": {
            "coap": {
                "connection_ref": "test-thermostat",
                "operation": "post",
                "args": {"path": "/things"},
            }
        },
        "project_id": "proj-test",
        # caller-side kwargs:
        "payload": b"hello",
        "content_format": "text/plain",
    }

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["args"] = dict(args)
        return {"code": "2.01", "payload": b"", "content_format": None}

    with patch(
        "sagewai.tools.executors.connections._coap_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    assert captured["args"]["path"] == "/things"
    assert captured["args"]["payload"] == b"hello"
    assert captured["args"]["content_format"] == "text/plain"


@pytest.mark.asyncio
async def test_psk_key_decrypted_before_runner(store, router):
    """Regression: encrypted ``psk_key`` must be decrypted before the runner
    sees it. Without decryption, aiocoap receives ``fernet:gAAAA...`` literal
    ciphertext and every DTLS handshake silently fails."""
    # Encrypt the psk_key the way admin POST / does, then write through the store.
    plaintext_pd = {
        "base_uri": "coaps://thermostat.example.com:5684",
        "use_dtls": True,
        "psk_identity": "device-01",
        "psk_key": "raw-psk-value-here",
        "default_timeout_seconds": 5.0,
    }
    encrypted_pd = router.encrypt(
        plaintext_pd,
        sensitive_field_paths=("psk_key",),
        connection_credentials_backend={"kind": "local"},
    )
    # Sanity: psk_key must now be ciphertext (fernet: prefix).
    assert encrypted_pd["psk_key"].startswith("fernet:"), (
        "test setup invariant: encrypt_fields should produce fernet:-prefixed ciphertext"
    )

    store.create(
        protocol="coap",
        project_id="proj-test",
        display_name="secure-thermostat",
        tags=["iot"],
        credentials_backend={"kind": "local"},
        protocol_data=encrypted_pd,
    )

    # On-disk inspection: confirm the stored record carries the ciphertext.
    persisted = store.list(project_id="proj-test", protocol="coap")[0]
    assert persisted.protocol_data["psk_key"].startswith("fernet:")

    captured: dict = {}

    async def _capture(connection, *, op, args):
        captured["psk_key"] = connection.protocol_data["psk_key"]
        captured["psk_identity"] = connection.protocol_data["psk_identity"]
        return {"code": "2.05", "payload": b"", "content_format": None}

    payload = {
        "_kind": "coap",
        "exec": {
            "coap": {
                "connection_ref": "secure-thermostat",
                "operation": "get",
                "args": {"path": "/.well-known/core"},
            }
        },
        "project_id": "proj-test",
    }

    with patch(
        "sagewai.tools.executors.connections._coap_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    # The runner MUST receive the plaintext psk_key, not the ciphertext.
    assert captured["psk_key"] == "raw-psk-value-here", (
        f"runner received {captured['psk_key']!r}, but expected plaintext "
        "'raw-psk-value-here' (decryption gap on dispatch path)"
    )
    # Non-sensitive fields pass through unchanged.
    assert captured["psk_identity"] == "device-01"


@pytest.mark.asyncio
async def test_executor_uses_router_when_provided(store, router):
    """The executor accepts an explicit router and threads it through decrypt.

    Tests that supply a router via kwarg drive the decrypt path; tests that
    omit it fall back to auto-bootstrap (covered in admin route tests).
    """
    encrypted = router.encrypt(
        {
            "base_uri": "coaps://device.example.com:5684",
            "use_dtls": True,
            "psk_identity": "id",
            "psk_key": "secret-key",
            "default_timeout_seconds": 5.0,
        },
        sensitive_field_paths=("psk_key",),
        connection_credentials_backend={"kind": "local"},
    )
    store.create(
        protocol="coap",
        project_id="proj-x",
        display_name="dev",
        tags=[],
        credentials_backend={"kind": "local"},
        protocol_data=encrypted,
    )

    payload = {
        "_kind": "coap",
        "exec": {
            "coap": {
                "connection_ref": "dev",
                "operation": "get",
                "args": {"path": "/"},
            }
        },
        "project_id": "proj-x",
    }

    seen_psk = []

    async def _capture(connection, *, op, args):
        seen_psk.append(connection.protocol_data["psk_key"])
        return {"code": "2.05", "payload": b"", "content_format": None}

    with patch(
        "sagewai.tools.executors.connections._coap_run_op",
        side_effect=_capture,
    ):
        await connections_run(payload, store=store, router=router)

    assert seen_psk == ["secret-key"]
