# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.datadog."""
import pytest
import respx

from sagewai.tools.builtins import datadog as dd_mod


def _creds(api_key: str = "dd-api", app_key: str = "dd-app", site: str = "datadoghq.com"):
    def _get(*, project_id, kind, id):
        return {
            "DATADOG_API_KEY": api_key,
            "DATADOG_APPLICATION_KEY": app_key,
            "DATADOG_SITE": site,
        }
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_search_logs_sends_both_headers():
    route = respx.post("https://api.datadoghq.com/api/v2/logs/events/search").respond(
        200, json={"data": [{"id": "log-1"}]},
    )
    out = await dd_mod.datadog_api(
        {
            "_operation": "search_logs",
            "filter": {"query": "service:web", "from": "now-15m", "to": "now"},
        },
        project_id="p1", get_credentials=_creds(api_key="real-api", app_key="real-app"),
    )
    assert out["data"][0]["id"] == "log-1"
    sent = route.calls.last.request
    assert sent.headers.get("DD-API-KEY") == "real-api"
    assert sent.headers.get("DD-APPLICATION-KEY") == "real-app"


@pytest.mark.asyncio
@respx.mock
async def test_base_url_switches_on_site():
    route = respx.post("https://api.datadoghq.eu/api/v2/logs/events/search").respond(
        200, json={"data": []},
    )
    await dd_mod.datadog_api(
        {"_operation": "search_logs", "filter": {"query": "x", "from": "now-1h", "to": "now"}},
        project_id="p1", get_credentials=_creds(site="datadoghq.eu"),
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_default_site_is_datadoghq_com():
    def _get(*, project_id, kind, id):
        return {"DATADOG_API_KEY": "k", "DATADOG_APPLICATION_KEY": "a"}

    route = respx.post("https://api.datadoghq.com/api/v2/logs/events/search").respond(
        200, json={"data": []},
    )
    await dd_mod.datadog_api(
        {"_operation": "search_logs", "filter": {"query": "x", "from": "now-1h", "to": "now"}},
        project_id="p1", get_credentials=_get,
    )
    assert route.called


@pytest.mark.asyncio
async def test_missing_application_key_raises():
    def _get(*, project_id, kind, id):
        return {"DATADOG_API_KEY": "k", "DATADOG_SITE": "datadoghq.com"}
    with pytest.raises(RuntimeError, match="DATADOG_APPLICATION_KEY"):
        await dd_mod.datadog_api(
            {"_operation": "search_logs", "filter": {"query": "x", "from": "now-1h", "to": "now"}},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
@respx.mock
async def test_submit_metric():
    respx.post("https://api.datadoghq.com/api/v2/series").respond(202, json={"errors": []})
    out = await dd_mod.datadog_api(
        {
            "_operation": "submit_metric",
            "series": [{"metric": "app.users", "points": [[1700000000, 42]], "type": 3, "tags": ["env:prod"]}],
        },
        project_id="p1", get_credentials=_creds(),
    )
    assert "errors" in out
