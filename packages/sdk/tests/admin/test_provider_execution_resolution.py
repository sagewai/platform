# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Provider resolution used by tenant-scoped execution paths."""

from __future__ import annotations

from sagewai.admin.autopilot_routes import _resolve_executor_config
from sagewai.admin.provider_resolution import litellm_kwargs_for_provider


class _UnexpectedFileStore:
    def list_providers_decrypted(self, project_id=None):  # pragma: no cover
        raise AssertionError("tenant provider list should be injected")


def test_provider_resolution_returns_explicit_litellm_kwargs():
    provider = {
        "provider_name": "openai",
        "default": True,
        "config": {"api_key": "sk-project", "model": "gpt-4o"},
    }
    assert litellm_kwargs_for_provider(provider) == {
        "model": "gpt-4o",
        "api_key": "sk-project",
    }


def test_autopilot_executor_config_uses_injected_tenant_provider():
    provider = {
        "provider_name": "openai",
        "default": True,
        "config": {"api_key": "sk-project", "model": "gpt-4o"},
    }
    cfg = _resolve_executor_config(
        _UnexpectedFileStore(),
        "project-a",
        providers=[provider],
    )
    assert cfg.model == "gpt-4o"
    assert cfg.api_key == "sk-project"
    assert cfg.allow_env_fallback is False


def test_autopilot_executor_config_disables_env_fallback_for_empty_tenant_providers():
    cfg = _resolve_executor_config(_UnexpectedFileStore(), "project-a", providers=[])
    assert cfg.allow_env_fallback is False
