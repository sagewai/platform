# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Plugin contract + PluginContext tests."""
from __future__ import annotations

import dataclasses

from sagewai.connections.protocols.base import (
    PluginContext,
    ProtocolPlugin,
    TestResult,
)
from sagewai.connections.store import ConnectionStore


def test_plugin_context_is_frozen_dataclass():
    """PluginContext is the service-locator plugins receive on every call."""
    assert dataclasses.is_dataclass(PluginContext)
    fields = {f.name for f in dataclasses.fields(PluginContext)}
    assert fields == {"store", "creds", "project_id", "request"}


def test_plugin_context_construction(tmp_path):
    """Smoke test the construction shape (creds + request may be None in PR2)."""
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(
        store=store,
        creds=None,  # PR3 lands the CredentialsBackendRouter; PR2 passes None
        project_id="default",
        request=None,
    )
    assert ctx.store is store
    assert ctx.project_id == "default"


def test_plugin_context_is_frozen(tmp_path):
    store = ConnectionStore(tmp_path / "s.json")
    ctx = PluginContext(store=store, creds=None, project_id="default", request=None)
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.project_id = "other"  # type: ignore[misc]


def test_test_result_is_reexported():
    """TestResult comes from sagewai.connections.models — re-exported for plugin convenience."""
    from sagewai.connections.models import TestResult as ModelsTestResult
    assert TestResult is ModelsTestResult


def test_protocol_plugin_is_a_typing_protocol():
    """ProtocolPlugin is a typing.Protocol — duck-typed contract for plugin classes."""
    # Protocol classes have _is_protocol = True
    assert getattr(ProtocolPlugin, "_is_protocol", False) is True


from sagewai.connections.protocols import (
    DEFAULT_KEY_FOR,
    PROTOCOLS,
    UnknownProtocolError,
    all_protocols,
    get_protocol,
)


def test_all_7_protocols_registered():
    ids = {p.id for p in all_protocols()}
    assert ids == {"http", "oauth2", "mcp", "inference", "sdk", "coap", "modbus"}


def test_get_protocol_unknown_raises():
    import pytest
    with pytest.raises(UnknownProtocolError):
        get_protocol("nope")


def test_protocols_have_unique_ids():
    ids = [p.id for p in PROTOCOLS]
    assert len(ids) == len(set(ids))


def test_every_protocol_implements_required_attributes():
    """Sanity check every plugin has the required ClassVar attributes."""
    for p in PROTOCOLS:
        assert isinstance(p.id, str) and p.id
        assert isinstance(p.display_name, str) and p.display_name
        assert isinstance(p.sensitive_fields, tuple)


def test_default_key_for_covers_oauth2_and_inference():
    """Plugins that need per-provider default semantics expose extractors."""
    assert "oauth2" in DEFAULT_KEY_FOR
    assert "inference" in DEFAULT_KEY_FOR


def test_every_plugin_satisfies_protocol():
    """Runtime-check the ProtocolPlugin contract for each registered plugin."""
    from sagewai.connections.protocols.base import ProtocolPlugin
    for p in PROTOCOLS:
        assert isinstance(p, ProtocolPlugin), f"{p.id} doesn't satisfy ProtocolPlugin"
