# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.braintree."""
import base64

import pytest
import respx

from sagewai.tools.builtins import braintree as bt_mod


def _creds(
    public_key: str = "pubkey",
    private_key: str = "privkey",
    merchant_id: str = "merchant-1",
    env: str = "sandbox",
):
    def _get(*, project_id, kind, id):
        return {
            "BRAINTREE_PUBLIC_KEY": public_key,
            "BRAINTREE_PRIVATE_KEY": private_key,
            "BRAINTREE_MERCHANT_ID": merchant_id,
            "BRAINTREE_ENV": env,
        }
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_transaction_sale_posts_graphql_mutation():
    route = respx.post("https://api.sandbox.braintreegateway.com/merchants/merchant-1/graphql").respond(
        200, json={"data": {"chargePaymentMethod": {"transaction": {"id": "tx-1", "status": "AUTHORIZED"}}}},
    )
    out = await bt_mod.braintree_api(
        {
            "_operation": "transaction_sale",
            "paymentMethodId": "pm-1",
            "amount": "10.00",
        },
        project_id="p1", get_credentials=_creds(),
    )
    assert out["data"]["chargePaymentMethod"]["transaction"]["id"] == "tx-1"
    body = route.calls.last.request.content.decode()
    assert "chargePaymentMethod" in body
    assert "pm-1" in body


@pytest.mark.asyncio
@respx.mock
async def test_base_url_contains_merchant_id():
    route = respx.post("https://api.sandbox.braintreegateway.com/merchants/acme-merch-99/graphql").respond(
        200, json={"data": {"createClientToken": {"clientToken": "ct-x"}}},
    )
    await bt_mod.braintree_api(
        {"_operation": "client_token_generate"},
        project_id="p1", get_credentials=_creds(merchant_id="acme-merch-99"),
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_production_base_url():
    route = respx.post("https://api.braintreegateway.com/merchants/m/graphql").respond(
        200, json={"data": {"createClientToken": {"clientToken": "ct-x"}}},
    )
    await bt_mod.braintree_api(
        {"_operation": "client_token_generate"},
        project_id="p1",
        get_credentials=_creds(merchant_id="m", env="production"),
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_basic_auth_header_constructed_from_public_and_private():
    route = respx.post("https://api.sandbox.braintreegateway.com/merchants/m/graphql").respond(
        200, json={"data": {}},
    )
    await bt_mod.braintree_api(
        {"_operation": "client_token_generate"},
        project_id="p1",
        get_credentials=_creds(public_key="pk", private_key="sk", merchant_id="m"),
    )
    expected = base64.b64encode(b"pk:sk").decode()
    assert route.calls.last.request.headers["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_missing_any_credential_raises():
    for missing_key in ("BRAINTREE_PUBLIC_KEY", "BRAINTREE_PRIVATE_KEY", "BRAINTREE_MERCHANT_ID"):
        def _get(missing=missing_key):
            def inner(*, project_id, kind, id):
                creds = {
                    "BRAINTREE_PUBLIC_KEY": "pk",
                    "BRAINTREE_PRIVATE_KEY": "sk",
                    "BRAINTREE_MERCHANT_ID": "m",
                    "BRAINTREE_ENV": "sandbox",
                }
                del creds[missing]
                return creds
            return inner
        with pytest.raises(RuntimeError, match=missing_key):
            await bt_mod.braintree_api(
                {"_operation": "client_token_generate"},
                project_id="p1", get_credentials=_get(),
            )
