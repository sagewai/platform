# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Autopilot blueprint-service base URL honors SAGEWAI_LLM_BASE_URL.

Regression: the autopilot routes built the SagewaiLLMClient with the config's
hardcoded default (https://llm.sagewai.ai), ignoring the SAGEWAI_LLM_BASE_URL
env var — so a self-hosted operator could not point Autopilot at their own
sagewai-llm and every blueprint retrieval failed (name-not-known / 503).
"""
from __future__ import annotations


def _setup_sf(tmp_path):
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=tmp_path / "admin-state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    return sf


def test_autopilot_config_honors_sagewai_llm_base_url_env(tmp_path, monkeypatch):
    from sagewai.admin.autopilot_state import get_autopilot_config

    sf = _setup_sf(tmp_path)
    monkeypatch.setenv("SAGEWAI_LLM_BASE_URL", "http://host.docker.internal:8100")
    assert get_autopilot_config(sf)["base_url"] == "http://host.docker.internal:8100"


def test_autopilot_config_default_base_url_without_env(tmp_path, monkeypatch):
    from sagewai.admin.autopilot_state import get_autopilot_config

    sf = _setup_sf(tmp_path)
    monkeypatch.delenv("SAGEWAI_LLM_BASE_URL", raising=False)
    assert get_autopilot_config(sf)["base_url"] == "https://llm.sagewai.ai"
