# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from __future__ import annotations
from sagewai.admin.state_file import AdminStateFile


def test_same_name_agents_coexist_across_projects(tmp_path):
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.create_agent({"name": "bot", "model": "a"}, project_id="proj-a")
    sf.create_agent({"name": "bot", "model": "b"}, project_id="proj-b")
    assert sf.get_agent("bot", project_id="proj-a")["model"] == "a"
    assert sf.get_agent("bot", project_id="proj-b")["model"] == "b"
    sf.delete_agent("bot", project_id="proj-a")
    assert sf.get_agent("bot", project_id="proj-a") is None
    assert sf.get_agent("bot", project_id="proj-b") is not None   # untouched


def test_same_name_providers_coexist_across_projects(tmp_path):
    sf = AdminStateFile(path=tmp_path / "s.json")
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "A"}, "project_id": "proj-a"})
    sf.upsert_provider({"provider_name": "openai", "config": {"api_key": "B"}, "project_id": "proj-b"})
    assert sf.get_provider_decrypted("openai", project_id="proj-a")["config"]["api_key"] == "A"
    assert sf.get_provider_decrypted("openai", project_id="proj-b")["config"]["api_key"] == "B"
    # deleting one project's provider leaves the other
    a_id = next(p["id"] for p in sf.list_providers(project_id="proj-a") if p["provider_name"] == "openai")
    sf.delete_provider(a_id)
    assert sf.get_provider_decrypted("openai", project_id="proj-a") is None
    assert sf.get_provider_decrypted("openai", project_id="proj-b") is not None
