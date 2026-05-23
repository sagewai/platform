# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.openfda."""
import pytest
import respx

from sagewai.tools.builtins import openfda as ofda_mod


def _creds(key="abc123"):
    def _get(*, project_id, kind, id):
        return {} if key is None else {"OPENFDA_API_KEY": key}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_drug_event_includes_api_key_when_present():
    route = respx.get("https://api.fda.gov/drug/event.json").respond(
        200, json={"results": []}
    )
    await ofda_mod.openfda(
        {"_operation": "drug_event", "search": 'patient.reaction.reactionmeddrapt:"nausea"'},
        project_id="p1",
        get_credentials=_creds(),
    )
    p = route.calls.last.request.url.params
    assert p["api_key"] == "abc123"
    assert p["search"]


@pytest.mark.asyncio
@respx.mock
async def test_omits_api_key_when_credential_absent():
    route = respx.get("https://api.fda.gov/drug/event.json").respond(
        200, json={"results": []}
    )
    await ofda_mod.openfda(
        {"_operation": "drug_event", "search": "x"},
        project_id="p1",
        get_credentials=_creds(key=None),
    )
    assert "api_key" not in route.calls.last.request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_each_op_hits_its_path():
    for op, path in [
        ("drug_label", "/drug/label.json"),
        ("device_event", "/device/event.json"),
        ("food_enforcement", "/food/enforcement.json"),
    ]:
        route = respx.get(f"https://api.fda.gov{path}").respond(200, json={"results": []})
        await ofda_mod.openfda(
            {"_operation": op, "search": "x"},
            project_id="p1",
            get_credentials=_creds(),
        )
        assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_limit_and_skip_pass_through():
    route = respx.get("https://api.fda.gov/drug/event.json").respond(
        200, json={"results": []}
    )
    await ofda_mod.openfda(
        {"_operation": "drug_event", "search": "x", "limit": 50, "skip": 10},
        project_id="p1",
        get_credentials=_creds(),
    )
    p = route.calls.last.request.url.params
    assert p["limit"] == "50"
    assert p["skip"] == "10"


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await ofda_mod.openfda(
            {"_operation": "delete", "search": "x"},
            project_id="p1",
            get_credentials=_creds(),
        )
