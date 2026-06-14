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
from sagewai.admin.provider_resolution import choose_provider, litellm_kwargs_for_provider


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


def test_choose_provider_prefers_project_default_over_older_org_default():
    chosen = choose_provider(
        [
            {
                "provider_name": "openai",
                "project_id": None,
                "default": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "config": {"api_key": "sk-org", "model": "gpt-org"},
            },
            {
                "provider_name": "anthropic",
                "project_id": "project-a",
                "default": True,
                "created_at": "2026-01-02T00:00:00+00:00",
                "config": {"api_key": "sk-project", "model": "claude-project"},
            },
        ]
    )
    assert chosen is not None
    assert chosen["project_id"] == "project-a"
    assert chosen["provider_name"] == "anthropic"


def test_choose_provider_project_row_shadows_same_named_org_provider():
    chosen = choose_provider(
        [
            {
                "provider_name": "openai",
                "project_id": None,
                "default": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "config": {"api_key": "sk-org", "model": "gpt-org"},
            },
            {
                "provider_name": "openai",
                "project_id": "project-a",
                "default": False,
                "created_at": "2026-01-02T00:00:00+00:00",
                "config": {"api_key": "sk-project", "model": "gpt-project"},
            },
        ]
    )
    assert chosen is not None
    assert chosen["project_id"] == "project-a"
    assert chosen["config"]["api_key"] == "sk-project"


def test_self_hosted_default_base_url_honors_ollama_host_env(monkeypatch):
    # In the bundled compose stack the backend runs in a container, so OLLAMA_HOST
    # points at the host gateway. A provider with no explicit base_url must still
    # reach Ollama on the host, not the container's localhost.
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    provider = {"provider_name": "ollama", "config": {"model": "qwen2.5:14b"}}
    kwargs = litellm_kwargs_for_provider(provider)
    assert kwargs["model"] == "ollama/qwen2.5:14b"
    assert kwargs["api_base"] == "http://host.docker.internal:11434"


def test_self_hosted_default_base_url_honors_lmstudio_host_env(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_HOST", "http://host.docker.internal:1234/v1")
    provider = {"provider_name": "lmstudio", "config": {"model": "google/gemma-3-4b"}}
    assert (
        litellm_kwargs_for_provider(provider)["api_base"]
        == "http://host.docker.internal:1234/v1"
    )


def test_self_hosted_default_base_url_falls_back_to_localhost(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    provider = {"provider_name": "ollama", "config": {"model": "llama3.2"}}
    assert litellm_kwargs_for_provider(provider)["api_base"] == "http://localhost:11434"


def test_explicit_provider_base_url_overrides_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    provider = {
        "provider_name": "ollama",
        "config": {"model": "llama3.2", "base_url": "http://gpu-box:11434"},
    }
    assert litellm_kwargs_for_provider(provider)["api_base"] == "http://gpu-box:11434"
