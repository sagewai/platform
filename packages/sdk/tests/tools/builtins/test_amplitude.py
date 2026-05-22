# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.amplitude."""
import json

import pytest
import respx

from sagewai.tools.builtins import amplitude as amp_mod


def _creds(key: str = "amp-test-key"):
    def _get(*, project_id, kind, id):
        return {"AMPLITUDE_API_KEY": key}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_track_event_injects_api_key_into_body():
    route = respx.post("https://api2.amplitude.com/2/httpapi").respond(
        200, json={"code": 200, "events_ingested": 1},
    )
    out = await amp_mod.amplitude_api(
        {
            "_operation": "track_event",
            "events": [{"user_id": "u-1", "event_type": "Button Clicked"}],
        },
        project_id="p1", get_credentials=_creds("amp-real"),
    )
    assert out["code"] == 200
    body = json.loads(route.calls.last.request.content.decode())
    assert body["api_key"] == "amp-real"
    assert body["events"] == [{"user_id": "u-1", "event_type": "Button Clicked"}]
    assert "Authorization" not in route.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_identify_user():
    route = respx.post("https://api2.amplitude.com/identify").respond(200, text="success")
    out = await amp_mod.amplitude_api(
        {
            "_operation": "identify_user",
            "identification": [{"user_id": "u-1", "user_properties": {"plan": "pro"}}],
        },
        project_id="p1", get_credentials=_creds(),
    )
    assert out["status"] == 200
    body = route.calls.last.request.content.decode()
    assert "api_key=" in body
    assert "identification=" in body


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await amp_mod.amplitude_api(
            {"_operation": "nope"},
            project_id="p1", get_credentials=_creds(),
        )


@pytest.mark.asyncio
async def test_missing_key_raises():
    def _empty(*, project_id, kind, id):
        return {}
    with pytest.raises(RuntimeError, match="AMPLITUDE_API_KEY"):
        await amp_mod.amplitude_api(
            {"_operation": "track_event", "events": []},
            project_id="p1", get_credentials=_empty,
        )
