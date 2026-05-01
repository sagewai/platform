# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared fixtures for real-world integration tests.

Requires:
- docker-compose.dev.yml services running (PostgreSQL, Milvus, NebulaGraph, Redis)
- LLM API keys in environment (.env)
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

# Map of provider name (used in parametrize IDs) to the required env var.
_PROVIDER_KEY_MAP: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "huggingface": "HF_TOKEN",
}


def _require_env(var: str) -> str:
    """Get required env var or skip test."""
    val = os.getenv(var)
    if not val:
        pytest.skip(f"{var} not set")
    return val


@pytest.fixture(autouse=True)
def _skip_if_missing_api_key(request):
    """Auto-skip parametrized integration tests when the provider's API key is absent.

    Inspects the test's parametrize values for a ``provider`` field and skips
    the test if the corresponding environment variable is not set.  Tests that
    are not parametrized with a provider are unaffected.
    """
    # callspec holds the parametrize values for the current test invocation
    callspec = getattr(request, "param", None)
    if callspec is None:
        # Try request.node.callspec (pytest internal but stable)
        callspec_obj = getattr(request.node, "callspec", None)
        if callspec_obj is not None:
            provider = callspec_obj.params.get("provider")
            if provider and provider in _PROVIDER_KEY_MAP:
                env_var = _PROVIDER_KEY_MAP[provider]
                if not os.getenv(env_var):
                    pytest.skip(f"{env_var} not set (provider: {provider})")


@pytest.fixture
def openai_key() -> str:
    return _require_env("OPENAI_API_KEY")


@pytest.fixture
def anthropic_key() -> str:
    return _require_env("ANTHROPIC_API_KEY")


@pytest.fixture
def google_key() -> str:
    return _require_env("GOOGLE_API_KEY")


@pytest.fixture
def mistral_key() -> str:
    return _require_env("MISTRAL_API_KEY")


@pytest.fixture
def groq_key() -> str:
    return _require_env("GROQ_API_KEY")


@pytest.fixture
def deepseek_key() -> str:
    return _require_env("DEEPSEEK_API_KEY")


@pytest.fixture
def hf_key() -> str:
    return _require_env("HF_TOKEN")


@pytest.fixture
def database_url() -> str:
    return os.getenv(
        "SAGEWAI_DATABASE_URL",
        "postgresql+asyncpg://sagecurator:sagecurator_password@localhost:5432/sagecurator",
    )


@pytest.fixture
def milvus_uri() -> str:
    return os.getenv("MILVUS_URI", "http://localhost:19530")


# Model lists for parametrized tests
# LiteLLM requires provider prefixes for non-OpenAI/Anthropic models.
# See: https://docs.litellm.ai/docs/providers
CHAT_MODELS = [
    ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("google", "gemini/gemini-2.5-flash"),
    ("mistral", "mistral/mistral-small-latest"),
    ("groq", "groq/llama-3.1-8b-instant"),
    pytest.param(
        "deepseek",
        "deepseek/deepseek-chat",
        marks=pytest.mark.xfail(
            reason="DeepSeek account balance may be insufficient", strict=False
        ),
    ),
    pytest.param(
        "huggingface",
        "huggingface/meta-llama/Llama-3.1-8B-Instruct",
        marks=pytest.mark.xfail(reason="HuggingFace model availability varies", strict=False),
    ),
]

TOOL_CALLING_MODELS = [
    ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("google", "gemini/gemini-2.5-flash"),
    ("mistral", "mistral/mistral-small-latest"),
    # Groq/Llama-8B excluded: tool calling unreliable on small models.
    # Tier 7 model comparison matrix still tests all models.
]

PREMIUM_MODELS = [
    ("openai", "gpt-4o"),
    ("anthropic", "claude-sonnet-4-5-20250514"),
    ("google", "gemini/gemini-2.5-flash-preview-04-17"),
]
