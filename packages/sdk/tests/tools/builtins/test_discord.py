# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.discord."""
import pytest
import respx

from sagewai.tools.builtins import discord as discord_mod


def _creds(token: str = "test-bot-token"):
    def _get(*, project_id, kind, id):
        return {"DISCORD_BOT_TOKEN": token}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_post_message_plain_content():
    route = respx.post("https://discord.com/api/v10/channels/123/messages").respond(
        200, json={"id": "456", "channel_id": "123", "content": "hello"},
    )
    out = await discord_mod.discord_api(
        {"channel_id": "123", "content": "hello"},
        project_id="p1", get_credentials=_creds("real-bot-token"),
    )
    assert out == {"id": "456", "channel_id": "123", "content": "hello"}
    assert route.calls.last.request.headers["Authorization"] == "Bot real-bot-token"


@pytest.mark.asyncio
@respx.mock
async def test_post_message_with_embeds():
    route = respx.post("https://discord.com/api/v10/channels/c/messages").respond(
        200, json={"id": "msg", "channel_id": "c", "content": ""},
    )
    await discord_mod.discord_api(
        {
            "channel_id": "c",
            "content": "",
            "embeds": [{"title": "T", "description": "D"}],
        },
        project_id="p1", get_credentials=_creds(),
    )
    body = route.calls.last.request.content.decode()
    assert '"embeds"' in body
    assert '"title": "T"' in body or '"title":"T"' in body


@pytest.mark.asyncio
async def test_content_over_2000_chars_raises_before_http():
    with pytest.raises(ValueError, match="2000"):
        await discord_mod.discord_api(
            {"channel_id": "c", "content": "x" * 2001},
            project_id="p1", get_credentials=_creds(),
        )


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_429_retries_once_then_degrades():
    """Discord returns 429 with retry_after in body and headers."""
    route = respx.post("https://discord.com/api/v10/channels/c/messages")
    route.respond(429, json={"retry_after": 0.01}, headers={"Retry-After": "0.01"})
    out = await discord_mod.discord_api(
        {"channel_id": "c", "content": "x"},
        project_id="p1", get_credentials=_creds(),
    )
    assert out == {"rate_limited": True}
