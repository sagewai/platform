# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.slack."""
import pytest
import respx

from sagewai.tools.builtins import slack as slack_mod


def _creds(token: str = "xoxb-test-token"):
    def _get(*, project_id, kind, id):
        return {"SLACK_BOT_TOKEN": token}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_post_message_plain_text():
    route = respx.post("https://slack.com/api/chat.postMessage").respond(
        200, json={"ok": True, "ts": "1700000000.000100", "channel": "C123"},
    )
    out = await slack_mod.post_to_slack(
        {"channel": "#general", "text": "hello"},
        project_id="p1",
        get_credentials=_creds("xoxb-abc"),
    )
    assert out == {"ok": True, "ts": "1700000000.000100", "channel": "C123"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer xoxb-abc"


@pytest.mark.asyncio
@respx.mock
async def test_post_message_with_thread_ts():
    respx.post("https://slack.com/api/chat.postMessage").respond(
        200, json={"ok": True, "ts": "1.2", "channel": "C123"},
    )
    out = await slack_mod.post_to_slack(
        {"channel": "C123", "text": "reply", "thread_ts": "1.1"},
        project_id="p1", get_credentials=_creds(),
    )
    assert out["ts"] == "1.2"


@pytest.mark.asyncio
@respx.mock
async def test_post_message_with_blocks():
    route = respx.post("https://slack.com/api/chat.postMessage").respond(
        200, json={"ok": True, "ts": "1.3", "channel": "C123"},
    )
    await slack_mod.post_to_slack(
        {
            "channel": "C123",
            "text": "fallback",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*bold*"}}],
        },
        project_id="p1", get_credentials=_creds(),
    )
    body = route.calls.last.request.content.decode()
    assert '"blocks"' in body
    assert '"section"' in body


@pytest.mark.asyncio
@respx.mock
async def test_slack_ok_false_surfaces_as_error():
    """Slack returns HTTP 200 with {ok: false, error: ...} on semantic failure."""
    respx.post("https://slack.com/api/chat.postMessage").respond(
        200, json={"ok": False, "error": "channel_not_found"},
    )
    with pytest.raises(slack_mod.SlackAPIError, match="channel_not_found"):
        await slack_mod.post_to_slack(
            {"channel": "#nope", "text": "x"},
            project_id="p1", get_credentials=_creds(),
        )
