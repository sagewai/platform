# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.compass."""
import base64

import pytest
import respx

from sagewai.tools.builtins import compass as cm_mod


def _creds(email: str = "ops@acme.com", token: str = "atl-tok", site: str = "https://acme.atlassian.net"):
    def _get(*, project_id, kind, id):
        return {"USERNAME": email, "PASSWORD": token, "COMPASS_SITE": site}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_get_component_posts_graphql_query():
    route = respx.post("https://acme.atlassian.net/gateway/api/graphql").respond(
        200, json={"data": {"compass": {"component": {"id": "ari:cloud:compass:component/abc", "name": "Web"}}}},
    )
    out = await cm_mod.compass_api(
        {"_operation": "get_component", "id": "ari:cloud:compass:component/abc"},
        project_id="p1", get_credentials=_creds(),
    )
    assert out["data"]["compass"]["component"]["name"] == "Web"
    body = route.calls.last.request.content.decode()
    assert "GetComponent" in body
    assert "ari:cloud:compass:component/abc" in body


@pytest.mark.asyncio
@respx.mock
async def test_basic_auth_from_email_and_token():
    route = respx.post("https://acme.atlassian.net/gateway/api/graphql").respond(
        200, json={"data": {}},
    )
    await cm_mod.compass_api(
        {"_operation": "get_component", "id": "x"},
        project_id="p1", get_credentials=_creds(email="ada@example.com", token="api-tok-123"),
    )
    expected = base64.b64encode(b"ada@example.com:api-tok-123").decode()
    assert route.calls.last.request.headers["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
@respx.mock
async def test_site_url_strips_trailing_slash():
    route = respx.post("https://acme.atlassian.net/gateway/api/graphql").respond(
        200, json={"data": {}},
    )
    await cm_mod.compass_api(
        {"_operation": "get_component", "id": "x"},
        project_id="p1", get_credentials=_creds(site="https://acme.atlassian.net/"),
    )
    assert route.called


@pytest.mark.asyncio
async def test_missing_site_raises():
    def _get(*, project_id, kind, id):
        return {"USERNAME": "e", "PASSWORD": "p"}
    with pytest.raises(RuntimeError, match="COMPASS_SITE"):
        await cm_mod.compass_api(
            {"_operation": "get_component", "id": "x"},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
@respx.mock
async def test_list_components_uses_list_query():
    route = respx.post("https://acme.atlassian.net/gateway/api/graphql").respond(
        200, json={"data": {"compass": {"searchComponents": {"nodes": []}}}},
    )
    await cm_mod.compass_api(
        {"_operation": "list_components", "cloudId": "abc-cloud-id"},
        project_id="p1", get_credentials=_creds(),
    )
    body = route.calls.last.request.content.decode()
    assert "searchComponents" in body
    assert "abc-cloud-id" in body
