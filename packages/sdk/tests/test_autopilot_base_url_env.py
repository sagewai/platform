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
    assert get_autopilot_config(sf)["base_url"] == "https://sw-autopilot-llm.sagewai.ai"


def test_stored_base_url_is_ignored(tmp_path, monkeypatch):
    """base_url is infra config — a value lingering in state is never used.

    base_url is resolved from env-or-default at read time, so a stale value (e.g.
    a now-dead default frozen into the state file by an older build) cannot make
    Autopilot call the wrong host. Custom self-hosted URLs go via the env var.
    """
    from sagewai.admin.autopilot_state import get_autopilot_config

    sf = _setup_sf(tmp_path)
    monkeypatch.delenv("SAGEWAI_LLM_BASE_URL", raising=False)
    # Simulate a stale value frozen into state by an older build.
    sf._mutate(lambda d: d.setdefault("autopilot", {}).update({"base_url": "https://llm.sagewai.ai"}))
    assert get_autopilot_config(sf)["base_url"] == "https://sw-autopilot-llm.sagewai.ai"


def test_set_autopilot_config_never_persists_base_url(tmp_path):
    """Enabling/patching config must not freeze base_url into the state file."""
    from sagewai.admin.autopilot_state import set_autopilot_config

    sf = _setup_sf(tmp_path)
    set_autopilot_config(sf, {"enabled": True, "tier": "anonymous"})
    assert "base_url" not in sf._read().get("autopilot", {})
