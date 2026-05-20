# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""End-to-end: catalog -> factory -> executor -> builtin for batch-2a tools."""
import pytest
import respx

from sagewai.tools import factory, registry


def _slack_creds(*, project_id, kind, id):
    return {"SLACK_BOT_TOKEN": "xoxb-x"}


def _discord_creds(*, project_id, kind, id):
    return {"DISCORD_BOT_TOKEN": "discord-x"}


def _email_creds(*, project_id, kind, id):
    return {"EMAIL_API_KEY": "re_test"}


def _mailchimp_creds(*, project_id, kind, id):
    return {"MAILCHIMP_API_KEY": "abc-us21"}


@pytest.mark.asyncio
@respx.mock
async def test_post_to_slack_via_factory():
    respx.post("https://slack.com/api/chat.postMessage").respond(
        200, json={"ok": True, "ts": "1.2", "channel": "C1"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_slack_creds)
    out = await callables["post_to_slack"]({
        "_operation": "post_message",
        "channel": "C1", "text": "hi",
    })
    assert out["ok"] is True


@pytest.mark.asyncio
@respx.mock
async def test_discord_via_factory():
    respx.post("https://discord.com/api/v10/channels/C1/messages").respond(
        200, json={"id": "m", "channel_id": "C1", "content": "hi"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_discord_creds)
    out = await callables["discord_api"]({
        "_operation": "post_message",
        "channel_id": "C1", "content": "hi",
    })
    assert out["id"] == "m"


@pytest.mark.asyncio
@respx.mock
async def test_email_send_via_factory():
    respx.post("https://api.resend.com/emails").respond(
        200, json={"id": "msg-1"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_email_creds)
    out = await callables["email_send"]({
        "to": "a@b.com", "from": "x@y.com", "subject": "S", "text": "T",
    })
    assert out["provider"] == "resend"


@pytest.mark.asyncio
@respx.mock
async def test_mailchimp_via_factory():
    respx.post("https://us21.api.mailchimp.com/3.0/lists/L1/members").respond(
        200, json={"id": "m", "email_address": "a@b.com", "status": "subscribed"},
    )
    registry._reset()
    registry.load()
    callables = factory.build_callables(project_id="p1", get_credentials=_mailchimp_creds)
    out = await callables["mailchimp_api"]({
        "_operation": "add_subscriber",
        "list_id": "L1", "email": "a@b.com",
    })
    assert out["status"] == "subscribed"
