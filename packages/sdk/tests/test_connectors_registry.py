# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
import pytest
from sagewai.connectors.registry import ConnectorRegistry
from sagewai.connectors.base import AuthField, AuthType, ConnectorSpec


def _make_spec(name: str = "test") -> ConnectorSpec:
    return ConnectorSpec(
        name=name, display_name=name.title(), category="test",
        description=f"Test {name}", auth_type=AuthType.NONE,
        auth_fields=[], mcp_command=["echo"],
    )


def test_register_and_get():
    reg = ConnectorRegistry()
    spec = _make_spec("slack")
    reg.register(spec)
    assert reg.get("slack").name == "slack"


def test_get_missing_raises():
    reg = ConnectorRegistry()
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_list_connectors():
    reg = ConnectorRegistry()
    reg.register(_make_spec("a"))
    reg.register(_make_spec("b"))
    names = [c.name for c in reg.list()]
    assert set(names) == {"a", "b"}


def test_catalog():
    reg = ConnectorRegistry()
    reg.register(_make_spec("slack"))
    cat = reg.catalog()
    assert len(cat) == 1
    assert cat[0]["name"] == "slack"
    assert "category" in cat[0]


@pytest.mark.asyncio
async def test_disconnect():
    from unittest.mock import AsyncMock, MagicMock
    reg = ConnectorRegistry()
    mock_conn = MagicMock()
    mock_conn.close = AsyncMock()
    reg._connections["test"] = mock_conn
    await reg.disconnect("test")
    mock_conn.close.assert_called_once()
    assert "test" not in reg._connections


@pytest.mark.asyncio
async def test_disconnect_all():
    from unittest.mock import AsyncMock, MagicMock
    reg = ConnectorRegistry()
    for name in ["a", "b", "c"]:
        mock_conn = MagicMock()
        mock_conn.close = AsyncMock()
        reg._connections[name] = mock_conn
    await reg.disconnect_all()
    assert len(reg._connections) == 0
