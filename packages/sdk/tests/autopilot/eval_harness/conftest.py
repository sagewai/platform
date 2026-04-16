# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared pytest fixtures for the eval_harness test suite."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from sagewai.autopilot.eval_harness.types import EvalConfig


@pytest.fixture()
def eval_config() -> EvalConfig:
    """Default eval config matching production thresholds."""
    return EvalConfig()


def make_retrieval_response(
    blueprint_id: str,
    top_score: float = 0.92,
    k: int = 5,
) -> list[dict]:
    """Build a fake retrieval response with one high-score hit and noise."""
    results = [{"id": blueprint_id, "score": top_score}]
    for i in range(1, k):
        results.append({"id": f"noise-bp-{i}", "score": max(0.0, top_score - i * 0.12)})
    return results


def make_mock_client(blueprint_id: str, top_score: float = 0.92, k: int = 5) -> AsyncMock:
    """Return a mock SagewaiLLMClient whose retrieve_blueprints returns canned data."""
    client = AsyncMock()
    response_payload = make_retrieval_response(blueprint_id, top_score, k)
    client.retrieve_blueprints.return_value = [
        {"blueprint_json": json.dumps({"id": r["id"]}), "score": r["score"]}
        for r in response_payload
    ]
    return client


@pytest.fixture()
def mock_client_factory():
    """Factory fixture: call with (blueprint_id, score) to get a ready mock client."""
    return make_mock_client
