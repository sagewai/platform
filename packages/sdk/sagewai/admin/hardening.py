# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Hostile-network response hardening (W6 of the multi-tenancy/RBAC roadmap).

Multi-tenant deployments are internet-facing, so add standard security response
headers and a request-size limit. **Gated to multi-tenant mode** so the single-
org self-hosted path (and its Playwright e2e) is byte-for-byte unchanged.

A pure-ASGI middleware so the body cap counts **actual** received bytes — a
``Content-Length``-only check is bypassable by a chunked/streamed request. The
``Content-Length`` header is a fast-path; the real enforcement counts the ASGI
receive stream and stops once it exceeds the cap. Security headers are injected
on every response, including the 413 rejection.

Scope: this is the response-header + body-size half of W6. The other W6 item —
**distributed** rate limiting / brute-force lockout (replacing the in-memory
single-process throttles) — needs a shared store and is tracked with the durable
Postgres work; it is intentionally not in this lean PR.
"""

from __future__ import annotations

import os

from starlette.datastructures import MutableHeaders

from sagewai.admin.tenancy import is_multi_tenant

_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
_TOO_LARGE_BODY = b'{"detail":"Request entity too large"}'


def _max_request_bytes() -> int:
    try:
        return int(os.environ.get("SAGEWAI_MAX_REQUEST_BYTES", str(_DEFAULT_MAX_BYTES)))
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _content_length(scope) -> int | None:
    for key, value in scope.get("headers") or []:
        if key == b"content-length" and value.isdigit():
            return int(value)
    return None


def _inject_headers(message) -> None:
    """Add the security response headers to an ``http.response.start`` message."""
    headers = MutableHeaders(scope=message)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    if os.environ.get("SAGEWAI_ADMIN_TLS", "") in {"1", "true"}:
        headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Pure-ASGI hardening (multi-tenant only): security headers + a true body cap.

    - ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``,
      ``Referrer-Policy: no-referrer`` on **every** response (incl. the 413);
      ``Strict-Transport-Security`` when ``SAGEWAI_ADMIN_TLS`` is set.
    - Reject a request whose body exceeds ``SAGEWAI_MAX_REQUEST_BYTES`` (default
      10 MiB) with 413 — counting actual received bytes, so chunked/streamed
      requests can't bypass it; ``Content-Length`` is only a fast-path.

    No-op in single-org mode (scope is organizational; e2e unchanged).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or not is_multi_tenant():
            await self.app(scope, receive, send)
            return

        max_bytes = _max_request_bytes()

        # Fast-path: an honest Content-Length over the cap — reject without reading.
        cl = _content_length(scope)
        if cl is not None and cl > max_bytes:
            await self._reject_too_large(send)
            return

        # Authoritative path: count actual body bytes (covers chunked / no CL),
        # bounded — we stop the moment the cap is exceeded, then replay downstream.
        body = bytearray()
        over = False
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            body += message.get("body", b"")
            if len(body) > max_bytes:
                over = True
                break
            if not message.get("more_body", False):
                break
        if over:
            await self._reject_too_large(send)
            return

        replayed = False

        async def _replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def _send(message):
            if message.get("type") == "http.response.start":
                _inject_headers(message)
            await send(message)

        await self.app(scope, _replay, _send)

    @staticmethod
    async def _reject_too_large(send) -> None:
        start = {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
            ],
        }
        _inject_headers(start)  # the rejection carries the hardening headers too
        await send(start)
        await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})
