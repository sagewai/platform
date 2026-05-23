# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.infermedica."""
import json

import pytest
import respx

from sagewai.tools.builtins import infermedica as im_mod


def _creds(app_id="id_abc", app_key="key_xyz"):
    def _get(*, project_id, kind, id):
        out = {}
        if app_id is not None:
            out["INFERMEDICA_APP_ID"] = app_id
        if app_key is not None:
            out["INFERMEDICA_APP_KEY"] = app_key
        return out
    return _get


_AGE = {"value": 35, "unit": "year"}
_EV = [{"id": "s_21", "choice_id": "present"}]


@pytest.mark.asyncio
@respx.mock
async def test_dual_headers_sent():
    route = respx.post("https://api.infermedica.com/v3/diagnosis").respond(
        200, json={"conditions": []}
    )
    await im_mod.infermedica(
        {"_operation": "diagnose", "sex": "male", "age": _AGE, "evidence": _EV},
        project_id="p1",
        get_credentials=_creds(),
    )
    req = route.calls.last.request
    assert req.headers["App-Id"] == "id_abc"
    assert req.headers["App-Key"] == "key_xyz"


@pytest.mark.asyncio
@respx.mock
async def test_parse_symptoms_posts_text_and_age():
    route = respx.post("https://api.infermedica.com/v3/parse").respond(
        200, json={"mentions": []}
    )
    await im_mod.infermedica(
        {"_operation": "parse_symptoms", "text": "headache and nausea", "age": _AGE},
        project_id="p1",
        get_credentials=_creds(),
    )
    body = json.loads(route.calls.last.request.content)
    assert body == {"text": "headache and nausea", "age": _AGE}


@pytest.mark.asyncio
@respx.mock
async def test_each_evidence_op_hits_its_path():
    for op, path in [
        ("suggest", "/suggest"),
        ("triage", "/triage"),
    ]:
        route = respx.post(f"https://api.infermedica.com/v3{path}").respond(
            200, json={}
        )
        await im_mod.infermedica(
            {"_operation": op, "sex": "female", "age": _AGE, "evidence": _EV},
            project_id="p1",
            get_credentials=_creds(),
        )
        assert route.called


@pytest.mark.asyncio
async def test_missing_app_id_raises():
    with pytest.raises(RuntimeError, match="INFERMEDICA_APP_ID"):
        await im_mod.infermedica(
            {"_operation": "diagnose", "sex": "male", "age": _AGE, "evidence": _EV},
            project_id="p1",
            get_credentials=_creds(app_id=None),
        )


@pytest.mark.asyncio
async def test_missing_app_key_raises():
    with pytest.raises(RuntimeError, match="INFERMEDICA_APP_KEY"):
        await im_mod.infermedica(
            {"_operation": "diagnose", "sex": "male", "age": _AGE, "evidence": _EV},
            project_id="p1",
            get_credentials=_creds(app_key=None),
        )


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await im_mod.infermedica(
            {"_operation": "delete"},
            project_id="p1",
            get_credentials=_creds(),
        )
