# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.email."""
import pytest
import respx

from sagewai.tools.builtins import email as email_mod


def _creds(key: str, provider: str | None = None):
    def _get(*, project_id, kind, id):
        out: dict[str, str] = {"EMAIL_API_KEY": key}
        if provider:
            out["EMAIL_PROVIDER"] = provider
        return out
    return _get


def test_detect_provider_resend():
    assert email_mod._detect_provider("re_xxxx", None) == "resend"


def test_detect_provider_sendgrid():
    assert email_mod._detect_provider("SG.xxx.yyy", None) == "sendgrid"


def test_detect_provider_postmark_via_explicit_field():
    assert email_mod._detect_provider("c0a8...uuid", "postmark") == "postmark"


def test_detect_provider_unknown_raises():
    with pytest.raises(email_mod.UnknownProviderError):
        email_mod._detect_provider("c0a8...uuid", None)


@pytest.mark.asyncio
@respx.mock
async def test_send_via_resend():
    route = respx.post("https://api.resend.com/emails").respond(
        200, json={"id": "msg-abc"},
    )
    out = await email_mod.email_send(
        {"to": "a@b.com", "from": "x@y.com", "subject": "S", "text": "T"},
        project_id="p1", get_credentials=_creds("re_test"),
    )
    assert out == {"provider": "resend", "message_id": "msg-abc", "status": 200}
    assert route.calls.last.request.headers["Authorization"] == "Bearer re_test"


@pytest.mark.asyncio
@respx.mock
async def test_send_via_sendgrid():
    respx.post("https://api.sendgrid.com/v3/mail/send").respond(
        202, headers={"X-Message-Id": "sg-msg-123"},
    )
    out = await email_mod.email_send(
        {"to": ["a@b.com", "c@d.com"], "from": "x@y.com", "subject": "S", "html": "<p>hi</p>"},
        project_id="p1", get_credentials=_creds("SG.test-key"),
    )
    assert out["provider"] == "sendgrid"
    assert out["status"] == 202
    assert out["message_id"] == "sg-msg-123"


@pytest.mark.asyncio
@respx.mock
async def test_send_via_postmark_explicit_provider():
    respx.post("https://api.postmarkapp.com/email").respond(
        200, json={"MessageID": "pm-uuid-1", "ErrorCode": 0, "Message": "OK"},
    )
    out = await email_mod.email_send(
        {"to": "a@b.com", "from": "x@y.com", "subject": "S", "text": "T"},
        project_id="p1",
        get_credentials=_creds("c0a82c98-uuid-style", provider="postmark"),
    )
    assert out["provider"] == "postmark"
    assert out["message_id"] == "pm-uuid-1"


@pytest.mark.asyncio
async def test_send_requires_text_or_html():
    with pytest.raises(ValueError, match="text"):
        await email_mod.email_send(
            {"to": "a@b.com", "from": "x@y.com", "subject": "S"},
            project_id="p1", get_credentials=_creds("re_test"),
        )
