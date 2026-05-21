# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2c tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _stripe_creds(*, project_id, kind, id):
    return {"STRIPE_TOKEN": "sk_test_x"}


def _adyen_creds(*, project_id, kind, id):
    return {"ADYEN_API_KEY": "adyen-key"}


def _plaid_creds(*, project_id, kind, id):
    return {"PLAID_CLIENT_ID": "cid", "PLAID_SECRET": "sec", "PLAID_ENV": "sandbox"}


def _braintree_creds(*, project_id, kind, id):
    return {
        "BRAINTREE_PUBLIC_KEY": "pk",
        "BRAINTREE_PRIVATE_KEY": "sk",
        "BRAINTREE_MERCHANT_ID": "m",
        "BRAINTREE_ENV": "sandbox",
    }


def _paypal_creds(*, project_id, kind, id):
    return {"PAYPAL_CLIENT_ID": "cid", "PAYPAL_SECRET": "sec", "PAYPAL_ENV": "sandbox"}


@pytest.fixture(autouse=True)
def _clear_paypal_cache():
    from sagewai.tools.builtins import paypal as paypal_mod
    paypal_mod._TOKEN_CACHE.clear()
    yield
    paypal_mod._TOKEN_CACHE.clear()


@pytest.mark.asyncio
@respx.mock
async def test_stripe_create_payment_intent_via_factory():
    route = respx.post("https://api.stripe.com/v1/payment_intents").respond(
        200,
        json={
            "id": "pi_1",
            "status": "requires_payment_method",
            "client_secret": "...",
            "amount": 100,
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_stripe_creds)
    out = await callables["stripe_api"]({
        "_operation": "create_payment_intent",
        "amount": 100,
        "currency": "usd",
    })
    assert out["id"] == "pi_1"
    assert route.calls.last.request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    )


@pytest.mark.asyncio
@respx.mock
async def test_adyen_payments_via_factory():
    respx.post("https://checkout-test.adyen.com/v71/payments").respond(
        200, json={"pspReference": "psp-1", "resultCode": "Authorised"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_adyen_creds)
    out = await callables["adyen_api"]({
        "_operation": "payments",
        "amount": {"value": 1000, "currency": "EUR"},
        "paymentMethod": {"type": "scheme"},
        "reference": "ref-1",
        "merchantAccount": "Acme",
    })
    assert out["resultCode"] == "Authorised"


@pytest.mark.asyncio
@respx.mock
async def test_plaid_link_token_create_via_factory():
    respx.post("https://sandbox.plaid.com/link/token/create").respond(
        200, json={"link_token": "link-sandbox-x", "expiration": "..."},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_plaid_creds)
    out = await callables["plaid_api"]({
        "_operation": "link_token_create",
        "user": {"client_user_id": "u"},
        "products": ["transactions"],
        "country_codes": ["US"],
    })
    assert out["link_token"].startswith("link-")


@pytest.mark.asyncio
@respx.mock
async def test_braintree_client_token_via_factory():
    respx.post("https://api.sandbox.braintreegateway.com/merchants/m/graphql").respond(
        200, json={"data": {"createClientToken": {"clientToken": "ct-x"}}},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_braintree_creds)
    out = await callables["braintree_api"]({"_operation": "client_token_generate"})
    assert out["data"]["createClientToken"]["clientToken"] == "ct-x"


@pytest.mark.asyncio
@respx.mock
async def test_paypal_create_order_via_factory():
    respx.post("https://api-m.sandbox.paypal.com/v1/oauth2/token").respond(
        200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 3600},
    )
    respx.post("https://api-m.sandbox.paypal.com/v2/checkout/orders").respond(
        201, json={"id": "ord-1", "status": "CREATED"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_paypal_creds)
    out = await callables["paypal_api"]({
        "_operation": "create_order",
        "intent": "CAPTURE",
        "purchase_units": [{"amount": {"currency_code": "USD", "value": "10.00"}}],
    })
    assert out["id"] == "ord-1"
