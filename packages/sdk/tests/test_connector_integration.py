# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Integration test: registry -> discover builtins -> catalog -> connect lifecycle."""

import pytest

from sagewai.connectors.registry import ConnectorRegistry
from sagewai.connectors.stores import InMemoryCredentialStore


@pytest.mark.asyncio
async def test_registry_discovers_builtins():
    """Registry should discover all builtin connectors."""
    reg = ConnectorRegistry()
    reg.discover_builtins()
    connectors = reg.list()
    names = {c.name for c in connectors}
    # 8 migrated + 2 aggregators + 4 social/commerce = 14
    assert "slack" in names
    assert "composio" in names
    assert "toolhouse" in names
    assert "whatsapp" in names
    assert "x" in names
    assert "instagram" in names
    assert "shopify" in names


def test_catalog_returns_all_connectors():
    reg = ConnectorRegistry()
    reg.discover_builtins()
    cat = reg.catalog()
    assert len(cat) >= 14  # 8 migrated + 2 aggregators + 4 new
    for item in cat:
        assert "name" in item
        assert "category" in item
        assert "auth_type" in item


@pytest.mark.asyncio
async def test_credential_resolver_flow():
    """Full flow: store creds -> registry resolves -> catalog shows configured."""
    store = InMemoryCredentialStore()
    await store.put("slack", {"bot_token": "xoxb-test"})
    reg = ConnectorRegistry(credential_store=store)
    reg.discover_builtins()
    creds = await reg._resolver.resolve(reg.get("slack"))
    assert creds["bot_token"] == "xoxb-test"
