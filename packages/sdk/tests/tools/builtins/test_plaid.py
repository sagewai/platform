# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.plaid."""
import pytest
import respx

from sagewai.tools.builtins import plaid as plaid_mod


def _creds(client_id: str = "cid", secret: str = "sec", env: str = "sandbox"):
    def _get(*, project_id, kind, id):
        return {"PLAID_CLIENT_ID": client_id, "PLAID_SECRET": secret, "PLAID_ENV": env}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_link_token_create_sends_both_headers():
    route = respx.post("https://sandbox.plaid.com/link/token/create").respond(
        200, json={"link_token": "link-sandbox-abc", "expiration": "..."},
    )
    out = await plaid_mod.plaid_api(
        {
            "_operation": "link_token_create",
            "user": {"client_user_id": "user-1"},
            "products": ["transactions"],
            "country_codes": ["US"],
        },
        project_id="p1", get_credentials=_creds(),
    )
    assert out["link_token"].startswith("link-")
    sent = route.calls.last.request
    assert sent.headers.get("PLAID-CLIENT-ID") == "cid"
    assert sent.headers.get("PLAID-SECRET") == "sec"


@pytest.mark.asyncio
@respx.mock
async def test_base_url_switches_on_env():
    respx.post("https://production.plaid.com/link/token/create").respond(
        200, json={"link_token": "link-prod-abc"},
    )
    out = await plaid_mod.plaid_api(
        {
            "_operation": "link_token_create",
            "user": {"client_user_id": "user-1"},
            "products": ["transactions"],
            "country_codes": ["US"],
        },
        project_id="p1", get_credentials=_creds(env="production"),
    )
    assert out["link_token"].startswith("link-prod-")


@pytest.mark.asyncio
async def test_missing_client_id_raises():
    def _get(*, project_id, kind, id):
        return {"PLAID_SECRET": "sec", "PLAID_ENV": "sandbox"}
    with pytest.raises(RuntimeError, match="PLAID_CLIENT_ID"):
        await plaid_mod.plaid_api(
            {"_operation": "link_token_create"},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
async def test_missing_secret_raises():
    def _get(*, project_id, kind, id):
        return {"PLAID_CLIENT_ID": "cid", "PLAID_ENV": "sandbox"}
    with pytest.raises(RuntimeError, match="PLAID_SECRET"):
        await plaid_mod.plaid_api(
            {"_operation": "link_token_create"},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
@respx.mock
async def test_default_env_is_sandbox():
    def _get(*, project_id, kind, id):
        return {"PLAID_CLIENT_ID": "cid", "PLAID_SECRET": "sec"}

    respx.post("https://sandbox.plaid.com/link/token/create").respond(
        200, json={"link_token": "link-sandbox-abc"},
    )
    out = await plaid_mod.plaid_api(
        {
            "_operation": "link_token_create",
            "user": {"client_user_id": "u"},
            "products": ["transactions"],
            "country_codes": ["US"],
        },
        project_id="p1", get_credentials=_get,
    )
    assert out["link_token"].startswith("link-sandbox-")
