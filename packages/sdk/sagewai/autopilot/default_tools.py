# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Default SDK-bundled tool implementations.

These are the callable entrypoints referenced in the tool catalog (kind: sdk).
Each function accepts a single ``payload`` dict and returns a dict.
"""
from __future__ import annotations

from typing import Any

_FETCH_LIMIT = 8_000
_USER_AGENT = "Mozilla/5.0 (Sagewai)"


async def fetch_url(payload: dict[str, Any]) -> dict[str, Any]:
    """GET a URL and return the response body (truncated to ``_FETCH_LIMIT`` chars).

    Accepts ``{"url": "https://…"}`` and returns
    ``{"url": "…", "status": <int>, "body": "…"}``.
    """
    try:
        import httpx
    except ImportError:
        return {"error": "fetch_url unavailable: httpx not installed"}

    url: str = payload.get("url", "")
    if not url.startswith(("http://", "https://")):
        return {"error": f"invalid url {url!r} — must start with http:// or https://"}

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15)
        body = resp.text[:_FETCH_LIMIT]
        return {"url": url, "status": resp.status_code, "body": body}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "url": url}
