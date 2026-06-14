# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Enrollment handshake on the client.

Before any signed call, a fresh install must exchange its instance id for the
server-derived HMAC secret (``HKDF(master, instance_id)``). A locally-generated
random secret can never validate. These tests pin that behaviour: the client
enrolls exactly once, adopts + persists the returned secret, signs subsequent
calls with it, and degrades sanely against a server that lacks the endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from sagewai.autopilot.sagewai_llm.cache import BlueprintCache
from sagewai.autopilot.sagewai_llm.client import SagewaiLLMClient
from sagewai.autopilot.sagewai_llm.identity import FileIdentityStore, ensure_identity

BASE_URL = "https://sw-autopilot-llm.sagewai.ai"
ENROLLED_SECRET = "ab" * 32  # 32-byte hex


def _build(tmp_path: Path) -> tuple[SagewaiLLMClient, FileIdentityStore]:
    store = FileIdentityStore(tmp_path / "identity.json")
    ident = ensure_identity(store)  # registered=False, random placeholder secret
    assert ident.registered is False
    cache = BlueprintCache(tmp_path / "cache", ttl_seconds=3600)
    client = SagewaiLLMClient(
        base_url=BASE_URL, identity=ident, cache=cache, identity_store=store
    )
    return client, store


@pytest.mark.asyncio
async def test_first_signed_call_enrolls_and_adopts_secret(
    tmp_path: Path, httpx_mock: HTTPXMock
):
    client, store = _build(tmp_path)
    instance_id = client.identity.instance_id

    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/instances/enroll",
        method="POST",
        json={"instance_id": instance_id, "instance_secret": ENROLLED_SECRET},
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/retrieve",
        method="POST",
        json={"candidates": []},
    )

    await client.retrieve_blueprints(goal="x", k=1)

    # The enrolled secret is adopted, marked registered, and persisted.
    assert client.identity.registered is True
    assert client.identity.instance_secret == ENROLLED_SECRET
    assert store.load().instance_secret == ENROLLED_SECRET
    assert store.load().registered is True

    # The enroll endpoint was sent the instance id.
    enroll_reqs = [r for r in httpx_mock.get_requests() if r.url.path == "/v1/instances/enroll"]
    assert len(enroll_reqs) == 1


@pytest.mark.asyncio
async def test_already_registered_identity_skips_enroll(
    tmp_path: Path, httpx_mock: HTTPXMock
):
    client, _ = _build(tmp_path)
    client._adopt_secret(ENROLLED_SECRET)  # simulate a prior enrollment

    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/retrieve", method="POST", json={"candidates": []}
    )
    await client.retrieve_blueprints(goal="x", k=1)

    # No enroll call should have been made.
    assert not [r for r in httpx_mock.get_requests() if "enroll" in r.url.path]


@pytest.mark.asyncio
async def test_server_without_enroll_endpoint_degrades(
    tmp_path: Path, httpx_mock: HTTPXMock
):
    """A 404 (older/dev server) marks registered so we don't retry every call."""
    client, _ = _build(tmp_path)
    placeholder = client.identity.instance_secret

    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/instances/enroll", method="POST", status_code=404
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/v1/blueprints/retrieve", method="POST", json={"candidates": []}
    )
    await client.retrieve_blueprints(goal="x", k=1)

    assert client.identity.registered is True
    assert client.identity.instance_secret == placeholder  # kept; dev servers ignore it
