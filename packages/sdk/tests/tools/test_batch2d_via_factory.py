# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2d tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _amplitude_creds(*, project_id, kind, id):
    return {"AMPLITUDE_API_KEY": "amp-test"}


def _opsgenie_creds(*, project_id, kind, id):
    return {"OPSGENIE_TOKEN": "genie-tok"}


def _datadog_creds(*, project_id, kind, id):
    return {
        "DATADOG_API_KEY": "dd-api",
        "DATADOG_APPLICATION_KEY": "dd-app",
        "DATADOG_SITE": "datadoghq.com",
    }


def _virustotal_creds(*, project_id, kind, id):
    return {"VT_API_KEY": "vt-key"}


def _snyk_creds(*, project_id, kind, id):
    return {"SNYK_TOKEN": "snyk-tok"}


def _atlassian_creds(*, project_id, kind, id):
    return {
        "USERNAME": "ops@acme.com",
        "PASSWORD": "atl-tok",
        "JIRA_SITE": "https://acme.atlassian.net",
        "CONFLUENCE_SITE": "https://acme.atlassian.net",
        "COMPASS_SITE": "https://acme.atlassian.net",
    }


def _adyen_live_creds(*, project_id, kind, id):
    return {
        "ADYEN_API_KEY": "adyen-key",
        "ADYEN_BASE_URL": "https://acme-checkout-live.adyenpayments.com/v71",
    }


@pytest.mark.asyncio
@respx.mock
async def test_amplitude_track_event_via_factory():
    respx.post("https://api2.amplitude.com/2/httpapi").respond(
        200, json={"code": 200, "events_ingested": 1},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_amplitude_creds)
    out = await callables["amplitude_api"]({
        "_operation": "track_event",
        "events": [{"user_id": "u-1", "event_type": "Test"}],
    })
    assert out["code"] == 200


@pytest.mark.asyncio
@respx.mock
async def test_opsgenie_create_alert_via_factory():
    route = respx.post("https://api.opsgenie.com/v2/alerts").respond(
        202, json={"result": "Request will be processed", "requestId": "req-1"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_opsgenie_creds)
    out = await callables["opsgenie_api"]({
        "_operation": "create_alert",
        "message": "Test alert",
    })
    assert out["result"] == "Request will be processed"
    assert route.calls.last.request.headers["Authorization"] == "GenieKey genie-tok"


@pytest.mark.asyncio
@respx.mock
async def test_datadog_search_logs_via_factory():
    respx.post("https://api.datadoghq.com/api/v2/logs/events/search").respond(
        200, json={"data": [{"id": "log-1"}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_datadog_creds)
    out = await callables["datadog_api"]({
        "_operation": "search_logs",
        "filter": {"query": "service:web", "from": "now-15m", "to": "now"},
    })
    assert out["data"][0]["id"] == "log-1"


@pytest.mark.asyncio
@respx.mock
async def test_virustotal_file_report_via_factory():
    respx.get("https://www.virustotal.com/api/v3/files/d41d8cd98f00b204e9800998ecf8427e").respond(
        200, json={"data": {"id": "d41d8cd98f00b204e9800998ecf8427e", "type": "file"}},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_virustotal_creds)
    out = await callables["virustotal_api"]({
        "_operation": "file_report",
        "hash": "d41d8cd98f00b204e9800998ecf8427e",
    })
    assert out["data"]["id"] == "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.asyncio
@respx.mock
async def test_snyk_list_orgs_via_factory():
    route = respx.get("https://api.snyk.io/rest/orgs").respond(
        200, json={"data": [{"id": "org-1", "attributes": {"name": "Acme"}}]},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_snyk_creds)
    out = await callables["snyk_api"]({
        "_operation": "list_orgs",
        "version": "2024-10-15",
    })
    assert out["data"][0]["id"] == "org-1"
    assert route.calls.last.request.headers["Authorization"] == "Token snyk-tok"


@pytest.mark.asyncio
@respx.mock
async def test_jira_myself_via_factory():
    respx.get("https://acme.atlassian.net/rest/api/3/myself").respond(
        200,
        json={
            "accountId": "acct-1",
            "displayName": "Ops Person",
            "emailAddress": "ops@acme.com",
        },
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_atlassian_creds)
    out = await callables["jira_api"]({"_operation": "myself"})
    assert out["accountId"] == "acct-1"


@pytest.mark.asyncio
@respx.mock
async def test_confluence_get_current_user_via_factory():
    respx.get("https://acme.atlassian.net/wiki/api/v2/users/current").respond(
        200, json={"accountId": "acct-1", "displayName": "Ops Person"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_atlassian_creds)
    out = await callables["confluence_api"]({"_operation": "get_current_user"})
    assert out["accountId"] == "acct-1"


@pytest.mark.asyncio
@respx.mock
async def test_compass_get_component_via_factory():
    respx.post("https://acme.atlassian.net/gateway/api/graphql").respond(
        200, json={"data": {"compass": {"component": {"id": "c-1", "name": "Web"}}}},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_atlassian_creds)
    out = await callables["compass_api"]({
        "_operation": "get_component",
        "id": "c-1",
    })
    assert out["data"]["compass"]["component"]["name"] == "Web"


@pytest.mark.asyncio
@respx.mock
async def test_adyen_live_url_override_via_factory():
    """When ADYEN_BASE_URL is set, request goes there instead of test URL."""
    respx.post("https://acme-checkout-live.adyenpayments.com/v71/payments").respond(
        200, json={"pspReference": "psp-live-1", "resultCode": "Authorised"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_adyen_live_creds)
    out = await callables["adyen_api"]({
        "_operation": "payments",
        "amount": {"value": 1000, "currency": "EUR"},
        "paymentMethod": {"type": "scheme"},
        "reference": "ref-1",
        "merchantAccount": "Acme",
    })
    assert out["resultCode"] == "Authorised"
