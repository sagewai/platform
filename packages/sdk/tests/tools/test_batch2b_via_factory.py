# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2b tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _github_creds(*, project_id, kind, id):
    return {"GITHUB_TOKEN": "github_pat_test"}


def _hubspot_creds(*, project_id, kind, id):
    return {"HUBSPOT_TOKEN": "pat-na1-test"}


def _greenhouse_creds(*, project_id, kind, id):
    return {"USERNAME": "greenhouse-api-key", "PASSWORD": ""}


def _maps_creds(*, project_id, kind, id):
    return {"MAPS_API_KEY": "AIza-test"}


# ── github (regression: existing get_repo still works) ──────────


@pytest.mark.asyncio
@respx.mock
async def test_github_get_repo_via_factory_regression():
    respx.get("https://api.github.com/repos/octocat/hello-world").respond(
        200,
        json={
            "id": 1,
            "name": "hello-world",
            "full_name": "octocat/hello-world",
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_github_creds)
    out = await callables["github"]({
        "_operation": "get_repo",
        "owner": "octocat",
        "repo": "hello-world",
    })
    assert out["name"] == "hello-world"


# ── github (new write ops) ──────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_github_create_issue_via_factory():
    respx.post("https://api.github.com/repos/octocat/hello-world/issues").respond(
        201,
        json={
            "id": 1,
            "number": 42,
            "html_url": "https://github.com/octocat/hello-world/issues/42",
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_github_creds)
    out = await callables["github"]({
        "_operation": "create_issue",
        "owner": "octocat",
        "repo": "hello-world",
        "title": "Test issue",
    })
    assert out["number"] == 42


@pytest.mark.asyncio
@respx.mock
async def test_github_search_code_via_factory():
    respx.get("https://api.github.com/search/code").respond(
        200,
        json={
            "total_count": 1,
            "items": [{"path": "README.md", "html_url": "...", "repository": {}}],
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_github_creds)
    out = await callables["github"]({
        "_operation": "search_code",
        "q": "extension:py addClass",
    })
    assert out["total_count"] == 1


# ── hubspot ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_hubspot_search_contacts_via_factory():
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts/search").respond(
        200,
        json={
            "results": [{"id": "1", "properties": {"email": "a@b.com"}}],
            "total": 1,
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_hubspot_creds)
    out = await callables["hubspot_api"]({
        "_operation": "search_contacts",
        "filterGroups": [
            {"filters": [{"propertyName": "email", "operator": "EQ", "value": "a@b.com"}]}
        ],
    })
    assert out["total"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_hubspot_create_contact_via_factory():
    respx.post("https://api.hubapi.com/crm/v3/objects/contacts").respond(
        201,
        json={
            "id": "abc",
            "properties": {"email": "x@y.com"},
            "createdAt": "2026-05-20T00:00:00Z",
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_hubspot_creds)
    out = await callables["hubspot_api"]({
        "_operation": "create_contact",
        "properties": {"email": "x@y.com", "firstname": "X"},
    })
    assert out["id"] == "abc"


# ── greenhouse ──────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_greenhouse_list_candidates_via_factory():
    import base64

    route = respx.get("https://harvest.greenhouse.io/v1/candidates").respond(
        200,
        json=[
            {
                "id": 1,
                "first_name": "A",
                "last_name": "B",
                "email_addresses": [],
                "applications": [],
            }
        ],
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_greenhouse_creds)
    out = await callables["greenhouse_api"]({"_operation": "list_candidates", "per_page": 1})
    assert isinstance(out, list)
    assert len(out) == 1
    # Basic-auth header — key as username, empty password
    expected = base64.b64encode(b"greenhouse-api-key:").decode()
    assert route.calls.last.request.headers["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
@respx.mock
async def test_greenhouse_create_candidate_via_factory():
    respx.post("https://harvest.greenhouse.io/v1/candidates").respond(
        201,
        json={"id": 99, "first_name": "Test", "last_name": "Candidate"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_greenhouse_creds)
    out = await callables["greenhouse_api"]({
        "_operation": "create_candidate",
        "first_name": "Test",
        "last_name": "Candidate",
        "email_addresses": [{"value": "t@c.com", "type": "personal"}],
    })
    assert out["id"] == 99


# ── maps_route ──────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_maps_route_via_factory():
    respx.get("https://maps.googleapis.com/maps/api/directions/json").respond(
        200,
        json={"status": "OK", "routes": [{"summary": "B96", "legs": []}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_maps_creds)
    out = await callables["maps_route"]({"origin": "Berlin", "destination": "Potsdam"})
    assert out["status"] == "OK"
