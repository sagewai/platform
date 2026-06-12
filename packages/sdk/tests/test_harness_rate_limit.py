# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-key request-rate limiting on the harness LLM gateway.

The gateway already enforces per-key *budget* caps; this proves the new
per-key *request-rate* limit: N requests with one key pass, the (N+1)th is
429'd with a ``Retry-After`` header, a second key keeps its own independent
budget, and the limit is disabled when ``<= 0``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sagewai.harness.app import create_harness_app
from sagewai.harness.models import HarnessKey
from sagewai.harness.store import InMemoryHarnessStore


class _StubBackend:
    """A backend that never makes a network call — returns a fixed response."""

    async def chat_completion(self, **_kwargs: Any) -> dict:
        return {
            "id": "stub",
            "object": "chat.completion",
            "model": "stub-model",
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    async def list_models(self) -> list[str]:
        return ["stub-model"]


async def _mint_key(store: InMemoryHarnessStore, name: str) -> str:
    """Create a harness key and return its plaintext (the Bearer token)."""
    return await store.create_key(HarnessKey(name=name, user_id=name))


def _app_with(store: InMemoryHarnessStore) -> TestClient:
    app = create_harness_app(backends={"default": _StubBackend()}, store=store)
    return TestClient(app)


def _post(client: TestClient, token: str) -> Any:
    return client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "stub-model", "messages": [{"role": "user", "content": "hi"}]},
    )


@pytest.mark.asyncio
async def test_within_limit_passes_then_429(monkeypatch):
    """Three requests pass; the fourth is 429 with a Retry-After header."""
    monkeypatch.setenv("SAGEWAI_HARNESS_RATE_LIMIT", "3")
    monkeypatch.setenv("SAGEWAI_HARNESS_RATE_WINDOW", "60")
    store = InMemoryHarnessStore()
    token = await _mint_key(store, "k1")
    client = _app_with(store)

    for _ in range(3):
        assert _post(client, token).status_code == 200
    blocked = _post(client, token)
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") == "60"


@pytest.mark.asyncio
async def test_second_key_has_its_own_budget(monkeypatch):
    """One key exhausting its budget must not throttle a different key."""
    monkeypatch.setenv("SAGEWAI_HARNESS_RATE_LIMIT", "2")
    monkeypatch.setenv("SAGEWAI_HARNESS_RATE_WINDOW", "60")
    store = InMemoryHarnessStore()
    tok_a = await _mint_key(store, "a")
    tok_b = await _mint_key(store, "b")
    client = _app_with(store)

    assert _post(client, tok_a).status_code == 200
    assert _post(client, tok_a).status_code == 200
    assert _post(client, tok_a).status_code == 429  # A is now over budget
    # B starts fresh — A's exhaustion must not bleed across keys.
    assert _post(client, tok_b).status_code == 200
    assert _post(client, tok_b).status_code == 200


@pytest.mark.asyncio
async def test_disabled_when_limit_non_positive(monkeypatch):
    """``SAGEWAI_HARNESS_RATE_LIMIT <= 0`` disables the limiter entirely."""
    monkeypatch.setenv("SAGEWAI_HARNESS_RATE_LIMIT", "0")
    monkeypatch.setenv("SAGEWAI_HARNESS_RATE_WINDOW", "60")
    store = InMemoryHarnessStore()
    token = await _mint_key(store, "k")
    client = _app_with(store)

    for _ in range(10):
        assert _post(client, token).status_code == 200
