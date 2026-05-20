# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.mailchimp."""
import pytest
import respx

from sagewai.tools.builtins import mailchimp as mc


def _creds(key: str = "abc123-us21"):
    def _get(*, project_id, kind, id):
        return {"MAILCHIMP_API_KEY": key}
    return _get


def test_parse_datacenter_happy():
    assert mc._parse_datacenter("abc123-us21") == "us21"


def test_parse_datacenter_missing_raises():
    with pytest.raises(ValueError, match="datacenter"):
        mc._parse_datacenter("abc123")


@pytest.mark.asyncio
@respx.mock
async def test_add_subscriber_happy():
    route = respx.post("https://us21.api.mailchimp.com/3.0/lists/L1/members").respond(
        200, json={"id": "m-1", "email_address": "a@b.com", "status": "subscribed"},
    )
    out = await mc.mailchimp_api(
        {
            "_operation": "add_subscriber",
            "list_id": "L1",
            "email": "a@b.com",
        },
        project_id="p1", get_credentials=_creds(),
    )
    assert out == {"id": "m-1", "email": "a@b.com", "status": "subscribed"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer abc123-us21"


@pytest.mark.asyncio
@respx.mock
async def test_add_subscriber_with_merge_fields_and_tags():
    route = respx.post("https://us21.api.mailchimp.com/3.0/lists/L1/members").respond(
        200, json={"id": "m-2", "email_address": "a@b.com", "status": "pending"},
    )
    await mc.mailchimp_api(
        {
            "_operation": "add_subscriber",
            "list_id": "L1",
            "email": "a@b.com",
            "status": "pending",
            "merge_fields": {"FNAME": "Ada"},
            "tags": ["customer"],
        },
        project_id="p1", get_credentials=_creds(),
    )
    body = route.calls.last.request.content.decode()
    assert '"merge_fields"' in body
    assert '"FNAME": "Ada"' in body or '"FNAME":"Ada"' in body
    assert '"tags"' in body


@pytest.mark.asyncio
@respx.mock
async def test_send_campaign_happy():
    respx.post("https://us21.api.mailchimp.com/3.0/campaigns/C1/actions/send").respond(204)
    out = await mc.mailchimp_api(
        {"_operation": "send_campaign", "campaign_id": "C1"},
        project_id="p1", get_credentials=_creds(),
    )
    assert out == {"sent": True, "campaign_id": "C1"}


@pytest.mark.asyncio
async def test_unknown_operation_raises():
    with pytest.raises(ValueError, match="unknown operation"):
        await mc.mailchimp_api(
            {"_operation": "nope", "list_id": "L1"},
            project_id="p1", get_credentials=_creds(),
        )
