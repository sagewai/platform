# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.paypal."""
import pytest
import respx

from sagewai.tools.builtins import paypal as paypal_mod


def _creds(client_id: str = "cid", secret: str = "sec", env: str = "sandbox"):
    def _get(*, project_id, kind, id):
        return {
            "PAYPAL_CLIENT_ID": client_id,
            "PAYPAL_SECRET": secret,
            "PAYPAL_ENV": env,
        }
    return _get


@pytest.fixture(autouse=True)
def _clear_token_cache():
    paypal_mod._TOKEN_CACHE.clear()
    yield
    paypal_mod._TOKEN_CACHE.clear()


@pytest.mark.asyncio
@respx.mock
async def test_first_call_exchanges_token_then_makes_api_call():
    token_route = respx.post("https://api-m.sandbox.paypal.com/v1/oauth2/token").respond(
        200, json={"access_token": "tok-1", "token_type": "Bearer", "expires_in": 3600},
    )
    order_route = respx.post("https://api-m.sandbox.paypal.com/v2/checkout/orders").respond(
        201, json={"id": "ord-1", "status": "CREATED"},
    )
    out = await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="p1", get_credentials=_creds(),
    )
    assert out["id"] == "ord-1"
    assert token_route.call_count == 1
    assert order_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_second_call_within_ttl_reuses_cached_token():
    token_route = respx.post("https://api-m.sandbox.paypal.com/v1/oauth2/token").respond(
        200, json={"access_token": "tok-1", "token_type": "Bearer", "expires_in": 3600},
    )
    order_route = respx.post("https://api-m.sandbox.paypal.com/v2/checkout/orders").respond(
        201, json={"id": "ord", "status": "CREATED"},
    )
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="p1", get_credentials=_creds(),
    )
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="p1", get_credentials=_creds(),
    )
    assert token_route.call_count == 1
    assert order_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_cache_evicts_after_ttl_expiry(monkeypatch):
    token_route = respx.post("https://api-m.sandbox.paypal.com/v1/oauth2/token").respond(
        200, json={"access_token": "tok-1", "token_type": "Bearer", "expires_in": 3600},
    )
    respx.post("https://api-m.sandbox.paypal.com/v2/checkout/orders").respond(
        201, json={"id": "o", "status": "CREATED"},
    )

    monkeypatch.setattr(paypal_mod.time, "monotonic", lambda: 0.0)
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="p1", get_credentials=_creds(),
    )
    assert token_route.call_count == 1

    monkeypatch.setattr(paypal_mod.time, "monotonic", lambda: 3541.0)
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="p1", get_credentials=_creds(),
    )
    assert token_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_cache_isolates_projects():
    token_route = respx.post("https://api-m.sandbox.paypal.com/v1/oauth2/token").respond(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600},
    )
    respx.post("https://api-m.sandbox.paypal.com/v2/checkout/orders").respond(
        201, json={"id": "o", "status": "CREATED"},
    )
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="proj-A", get_credentials=_creds(),
    )
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="proj-B", get_credentials=_creds(),
    )
    assert token_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_live_env_uses_live_endpoint():
    token_route = respx.post("https://api-m.paypal.com/v1/oauth2/token").respond(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600},
    )
    respx.post("https://api-m.paypal.com/v2/checkout/orders").respond(
        201, json={"id": "o", "status": "CREATED"},
    )
    await paypal_mod.paypal_api(
        {"_operation": "create_order", "intent": "CAPTURE", "purchase_units": []},
        project_id="p1", get_credentials=_creds(env="live"),
    )
    assert token_route.called


@pytest.mark.asyncio
async def test_missing_credentials_raise():
    def _empty(*, project_id, kind, id):
        return {}
    with pytest.raises(RuntimeError, match="PAYPAL_CLIENT_ID"):
        await paypal_mod.paypal_api(
            {"_operation": "create_order"},
            project_id="p1", get_credentials=_empty,
        )
