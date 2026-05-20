# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Email send tool — Resend / SendGrid / Postmark via provider auto-detect.

Detection rules:
- ``re_*``    → Resend
- ``SG.*``    → SendGrid
- (other)     → requires an explicit ``EMAIL_PROVIDER`` credential field
                (typically ``postmark`` since Postmark keys are bare UUIDs)
"""
from __future__ import annotations

from typing import Any, Callable

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


class UnknownProviderError(RuntimeError):
    """Key prefix didn't match a known provider and no explicit provider given."""


def _detect_provider(api_key: str, explicit_provider: str | None) -> str:
    if api_key.startswith("re_"):
        return "resend"
    if api_key.startswith("SG."):
        return "sendgrid"
    if explicit_provider:
        return explicit_provider
    raise UnknownProviderError(
        f"unable to detect email provider from key {api_key[:6]!r}...; "
        "set the connection record's provider field explicitly"
    )


def _validate_body_payload(payload: dict[str, Any]) -> None:
    if not (payload.get("text") or payload.get("html")):
        raise ValueError("email_send requires at least one of `text` or `html`")


async def _send_resend(client, api_key, payload):
    body = {
        "from": payload["from"],
        "to": payload["to"] if isinstance(payload["to"], list) else [payload["to"]],
        "subject": payload["subject"],
    }
    if payload.get("text"):
        body["text"] = payload["text"]
    if payload.get("html"):
        body["html"] = payload["html"]
    if payload.get("reply_to"):
        body["reply_to"] = payload["reply_to"]
    resp = await client.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
    )
    resp.raise_for_status()
    return {"provider": "resend", "message_id": resp.json().get("id", ""), "status": resp.status_code}


async def _send_sendgrid(client, api_key, payload):
    to_list = payload["to"] if isinstance(payload["to"], list) else [payload["to"]]
    content: list[dict[str, str]] = []
    if payload.get("text"):
        content.append({"type": "text/plain", "value": payload["text"]})
    if payload.get("html"):
        content.append({"type": "text/html", "value": payload["html"]})
    body = {
        "personalizations": [{"to": [{"email": e} for e in to_list]}],
        "from": {"email": payload["from"]},
        "subject": payload["subject"],
        "content": content,
    }
    if payload.get("reply_to"):
        body["reply_to"] = {"email": payload["reply_to"]}
    resp = await client.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
    )
    resp.raise_for_status()
    return {
        "provider": "sendgrid",
        "message_id": resp.headers.get("X-Message-Id", ""),
        "status": resp.status_code,
    }


async def _send_postmark(client, api_key, payload):
    to_str = ", ".join(payload["to"]) if isinstance(payload["to"], list) else payload["to"]
    body = {
        "From": payload["from"],
        "To": to_str,
        "Subject": payload["subject"],
    }
    if payload.get("text"):
        body["TextBody"] = payload["text"]
    if payload.get("html"):
        body["HtmlBody"] = payload["html"]
    if payload.get("reply_to"):
        body["ReplyTo"] = payload["reply_to"]
    resp = await client.post(
        "https://api.postmarkapp.com/email",
        headers={
            "X-Postmark-Server-Token": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"provider": "postmark", "message_id": str(data.get("MessageID", "")), "status": resp.status_code}


_DISPATCH = {
    "resend": _send_resend,
    "sendgrid": _send_sendgrid,
    "postmark": _send_postmark,
}


async def email_send(
    payload: dict[str, Any],
    *,
    project_id: str,
    get_credentials: Callable[..., dict[str, str]],
) -> dict[str, Any]:
    """Send a transactional email via Resend, SendGrid, or Postmark."""
    _validate_body_payload(payload)

    creds = get_credentials(project_id=project_id, kind="tool", id="email_send")
    api_key = creds.get("EMAIL_API_KEY", "")
    if not api_key:
        raise RuntimeError("email_send: missing EMAIL_API_KEY credential")
    explicit_provider = creds.get("EMAIL_PROVIDER")
    provider = _detect_provider(api_key, explicit_provider)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        return await _DISPATCH[provider](client, api_key, payload)
