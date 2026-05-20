# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest
from sagewai.tools import registry


def setup_function(_):
    registry._reset()


def test_lookup_unknown_raises():
    registry.load()
    with pytest.raises(registry.ToolNotFoundError):
        registry.lookup("nope_not_real")


def test_load_is_idempotent():
    registry.load()
    first = registry.lookup_or_none("fetch_url")
    registry.load()
    second = registry.lookup_or_none("fetch_url")
    # Seeds land in Task A3; for now both None is fine.
    assert first == second


def test_list_by_tier_returns_partition(tmp_path, monkeypatch):
    (tmp_path / "_schema.json").write_text((registry._CATALOG_DIR / "_schema.json").read_text())
    (tmp_path / "tool_a.yaml").write_text("""
id: tool_a
version: 0.1.0
title: A
description: a
category: test
kind: sdk
sandbox_tier: SANDBOXED
exec: {sdk: {entrypoint: pkg.mod:fn}}
scopes: []
setup: {auth_complexity: none, body: x}
""")
    (tmp_path / "tool_b.yaml").write_text("""
id: tool_b
version: 0.1.0
title: B
description: b
category: test
kind: sdk
sandbox_tier: SANDBOXED
exec: {sdk: {entrypoint: pkg.mod:fn}}
scopes: []
setup: {auth_complexity: api_key, body: x}
""")
    monkeypatch.setattr(registry, "_CATALOG_DIR", tmp_path)
    registry._reset()
    registry.load()
    assert {e.id for e in registry.list_by_tier("none")} == {"tool_a"}
    assert {e.id for e in registry.list_by_tier("api_key")} == {"tool_b"}


def test_duplicate_id_is_fatal(tmp_path, monkeypatch):
    (tmp_path / "_schema.json").write_text((registry._CATALOG_DIR / "_schema.json").read_text())
    body = """
id: dup
version: 0.1.0
title: D
description: d
category: test
kind: sdk
sandbox_tier: SANDBOXED
exec: {sdk: {entrypoint: pkg.mod:fn}}
scopes: []
setup: {auth_complexity: none, body: x}
"""
    (tmp_path / "first.yaml").write_text(body)
    (tmp_path / "second.yaml").write_text(body)
    monkeypatch.setattr(registry, "_CATALOG_DIR", tmp_path)
    registry._reset()
    with pytest.raises(registry.CatalogError, match="duplicate id"):
        registry.load()


def test_malformed_yaml_is_fatal(tmp_path, monkeypatch):
    (tmp_path / "_schema.json").write_text((registry._CATALOG_DIR / "_schema.json").read_text())
    (tmp_path / "bad.yaml").write_text("id: bad\nkind: sdk\n# missing required fields\n")
    monkeypatch.setattr(registry, "_CATALOG_DIR", tmp_path)
    registry._reset()
    with pytest.raises(registry.CatalogError):
        registry.load()


def test_scopes_for_returns_frozenset():
    registry.load()
    assert isinstance(registry.scopes_for("nonexistent"), frozenset)


def test_seed_entries_load():
    registry._reset()
    registry.load()
    assert registry.lookup("fetch_url").kind == "sdk"
    assert registry.lookup("github").kind == "http"
    assert registry.lookup("filesystem_mcp").kind == "mcp"


def test_seed_scopes_for_github():
    registry._reset()
    registry.load()
    assert "network.outbound.fetch" in registry.scopes_for("github")
    assert "secrets.github_token" in registry.scopes_for("github")
