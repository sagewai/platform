# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Connection dataclass + supporting types tests."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from sagewai.connections.models import (
    Connection,
    ConnectionStatus,
    HealthResult,
    TestResult,
    valid_protocol_ids,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_connection_construction_minimal_fields():
    c = Connection(
        id="conn_oauth2_abc",
        protocol="oauth2",
        project_id="default",
        display_name="Test",
        tags=("music",),
        credentials_backend=None,
        status="pending",
        last_tested_at=None,
        last_test_ok=None,
        is_default=False,
        created_at=_now(),
        updated_at=_now(),
        last_error=None,
        protocol_data={"foo": "bar"},
    )
    assert c.id == "conn_oauth2_abc"
    assert c.kind == "connection"  # discriminator is fixed
    assert c.protocol_data == {"foo": "bar"}
    assert c.tags == ("music",)


def test_connection_is_frozen():
    c = Connection(
        id="conn_x",
        protocol="oauth2",
        project_id=None,
        display_name="X",
        tags=(),
        credentials_backend=None,
        status="ready",
        last_tested_at=None,
        last_test_ok=None,
        is_default=False,
        created_at=_now(),
        updated_at=_now(),
        last_error=None,
        protocol_data={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.display_name = "Y"  # type: ignore[misc]


def test_connection_status_enum_values():
    """Generic statuses match the spec §6.5 list."""
    assert set(ConnectionStatus.__args__) == {
        "ready", "pending", "expired", "revoked", "error",
    }


def test_test_result_shape():
    r = TestResult(ok=True, status_code=200, message="hello")
    assert r.ok is True
    assert r.status_code == 200
    assert r.message == "hello"


def test_health_result_shape():
    r = HealthResult(ok=False, message="missing master key")
    assert r.ok is False
    assert r.message == "missing master key"


def test_valid_protocol_ids_defaults_to_five():
    """The default allowed-protocols tuple matches the spec §3 list."""
    assert valid_protocol_ids() == ("http", "oauth2", "mcp", "inference", "sdk")
