# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.terra."""
import pytest
import respx

from sagewai.tools.builtins import terra as terra_mod


def _creds(api_key="tk_abc", dev_id="dev_xyz"):
    def _get(*, project_id, kind, id):
        out = {}
        if api_key is not None:
            out["TERRA_API_KEY"] = api_key
        if dev_id is not None:
            out["TERRA_DEV_ID"] = dev_id
        return out
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_dual_headers_sent():
    route = respx.get("https://api.tryterra.co/v2/userInfo").respond(
        200, json={"users": []}
    )
    await terra_mod.terra(
        {"_operation": "list_users"}, project_id="p1", get_credentials=_creds()
    )
    req = route.calls.last.request
    assert req.headers["x-api-key"] == "tk_abc"
    assert req.headers["dev-id"] == "dev_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_get_daily_sends_date_range():
    route = respx.get("https://api.tryterra.co/v2/daily").respond(
        200, json={"data": []}
    )
    await terra_mod.terra(
        {
            "_operation": "get_daily",
            "user_id": "u1",
            "start_date": "2026-05-01",
            "end_date": "2026-05-07",
        },
        project_id="p1",
        get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert req.url.params["user_id"] == "u1"
    assert req.url.params["start_date"] == "2026-05-01"
    assert req.url.params["end_date"] == "2026-05-07"


@pytest.mark.asyncio
@respx.mock
async def test_each_summary_op_hits_its_path():
    for op, path in [
        ("get_sleep", "/sleep"),
        ("get_body", "/body"),
        ("get_activity", "/activity"),
    ]:
        route = respx.get(f"https://api.tryterra.co/v2{path}").respond(
            200, json={"data": []}
        )
        await terra_mod.terra(
            {
                "_operation": op,
                "user_id": "u1",
                "start_date": "2026-05-01",
                "end_date": "2026-05-07",
            },
            project_id="p1",
            get_credentials=_creds(),
        )
        assert route.called


@pytest.mark.asyncio
async def test_missing_api_key_raises():
    with pytest.raises(RuntimeError, match="TERRA_API_KEY"):
        await terra_mod.terra(
            {"_operation": "list_users"},
            project_id="p1",
            get_credentials=_creds(api_key=None),
        )


@pytest.mark.asyncio
async def test_missing_dev_id_raises():
    with pytest.raises(RuntimeError, match="TERRA_DEV_ID"):
        await terra_mod.terra(
            {"_operation": "list_users"},
            project_id="p1",
            get_credentials=_creds(dev_id=None),
        )


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await terra_mod.terra(
            {"_operation": "delete_user"},
            project_id="p1",
            get_credentials=_creds(),
        )
