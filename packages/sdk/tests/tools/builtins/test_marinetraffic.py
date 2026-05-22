# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.marinetraffic."""
import pytest
import respx

from sagewai.tools.builtins import marinetraffic as mt_mod


def _creds(key="mt-key-123"):
    def _get(*, project_id, kind, id):
        return {"MARINETRAFFIC_API_KEY": key}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_key_embedded_in_path():
    url = "https://services.marinetraffic.com/api/exportvessel/v:8/mt-key-123/protocol:jsono"
    respx.get(url).respond(200, json=[{"MMSI": 123}])
    out = await mt_mod.marinetraffic(
        {"_operation": "vessel_positions"},
        project_id="p1", get_credentials=_creds(),
    )
    assert out["data"][0]["MMSI"] == 123


@pytest.mark.asyncio
@respx.mock
async def test_params_appended_as_colon_segments():
    url = ("https://services.marinetraffic.com/api/exportvessel/v:8/"
           "mt-key-123/timespan:10/protocol:jsono")
    route = respx.get(url).respond(200, json=[])
    await mt_mod.marinetraffic(
        {"_operation": "vessel_positions", "params": {"timespan": 10}},
        project_id="p1", get_credentials=_creds(),
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_port_calls_uses_correct_service_and_version():
    url = "https://services.marinetraffic.com/api/portcalls/v:5/mt-key-123/protocol:jsono"
    route = respx.get(url).respond(200, json=[])
    await mt_mod.marinetraffic(
        {"_operation": "port_calls"},
        project_id="p1", get_credentials=_creds(),
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_vessel_details_uses_masterdata_service():
    url = "https://services.marinetraffic.com/api/vesselmasterdata/v:1/mt-key-123/protocol:jsono"
    route = respx.get(url).respond(200, json=[])
    await mt_mod.marinetraffic(
        {"_operation": "vessel_details"},
        project_id="p1", get_credentials=_creds(),
    )
    assert route.called


@pytest.mark.asyncio
async def test_missing_key_raises():
    def _get(*, project_id, kind, id):
        return {}
    with pytest.raises(RuntimeError, match="MARINETRAFFIC_API_KEY"):
        await mt_mod.marinetraffic(
            {"_operation": "vessel_positions"},
            project_id="p1", get_credentials=_get,
        )


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await mt_mod.marinetraffic(
            {"_operation": "sink_ship"},
            project_id="p1", get_credentials=_creds(),
        )
