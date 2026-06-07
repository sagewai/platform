# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AdminStateFile SHARED_ONLY scope: org scope reads/deletes org-shared only (W4)."""

from sagewai.admin.state_file import SHARED_ONLY, AdminStateFile


def test_filter_shared_only_vs_project_vs_all(tmp_path):
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.create_agent({"name": "shared"}, project_id=None)
    sf.create_agent({"name": "a"}, project_id="pA")
    sf.create_agent({"name": "b"}, project_id="pB")

    # SHARED_ONLY -> org-shared rows only (the multi-tenant org-scope read).
    assert {a["name"] for a in sf.list_agents(SHARED_ONLY)} == {"shared"}
    # A project -> its own rows plus org-shared (inherited).
    assert {a["name"] for a in sf.list_agents("pA")} == {"a", "shared"}
    # None -> legacy "all" view (single-org mode only).
    assert {a["name"] for a in sf.list_agents(None)} == {"shared", "a", "b"}


def test_delete_agent_shared_only_does_not_span_projects(tmp_path):
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.create_agent({"name": "same"}, project_id="pA")
    sf.create_agent({"name": "same"}, project_id="pB")
    sf.create_agent({"name": "same"}, project_id=None)  # org-shared

    # Org-scope delete removes only the org-shared copy (not A/B) — the bug fix.
    assert sf.delete_agent("same", project_id=SHARED_ONLY) is True
    assert {a.get("project_id") for a in sf.list_agents(None)} == {"pA", "pB"}

    # Project-scope delete removes only that project's copy.
    assert sf.delete_agent("same", project_id="pA") is True
    assert {a.get("project_id") for a in sf.list_agents(None)} == {"pB"}


def test_set_default_provider_shared_only_actually_sets_flag(tmp_path):
    # Org-shared default must really persist default=True (the sentinel is
    # normalized to the stored None scope for the update, not just the lookup).
    sf = AdminStateFile(path=tmp_path / "p.json")
    sf.upsert_provider({"provider_name": "openai", "config": {}})  # org-shared
    result = sf.set_default_provider("openai", project_id=SHARED_ONLY)
    assert result is not None
    assert result["default"] is True
    persisted = sf.list_providers(SHARED_ONLY)
    assert any(p.get("provider_name") == "openai" and p.get("default") for p in persisted)
