# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HTTP fetch + parsing utilities.

Tools:
- fetch_url    — GET a URL, return truncated body
- web_scrape   — fetch + extract main content via readability
- web_search   — DuckDuckGo backend, no key required
- pdf_parse    — read text from PDF (url or bytes_b64)
"""
from __future__ import annotations

import base64
import io
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import httpx

from duckduckgo_search import DDGS

_FETCH_LIMIT = 8_000
_UA = "Mozilla/5.0 (Sagewai)"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


# ── fetch_url ────────────────────────────────────────────────────


async def fetch_url(payload: dict[str, Any]) -> dict[str, Any]:
    """GET a URL and return the response body (truncated to ``_FETCH_LIMIT`` chars)."""
    url: str = payload.get("url", "")
    if not url.startswith(("http://", "https://")):
        return {"error": f"invalid url {url!r} — must start with http:// or https://"}
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA},
        ) as client:
            resp = await client.get(url)
        body = resp.text[:_FETCH_LIMIT]
        return {"url": url, "status": resp.status_code, "body": body}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "url": url}


# ── web_scrape ───────────────────────────────────────────────────


class _TextStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join("".join(self._parts).split())


_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_links(html: str, *, base: str, limit: int) -> list[str]:
    out: list[str] = []
    for match in _HREF_RE.finditer(html):
        href = match.group(1)
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        out.append(urljoin(base, href))
        if len(out) >= limit:
            break
    return out


async def web_scrape(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch a URL and extract main readable content via readability."""
    from readability import Document

    url = payload["url"]
    max_chars = int(payload.get("max_chars", _FETCH_LIMIT))
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA},
        ) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}", "url": url}

    title: str | None = None
    text = ""
    try:
        doc = Document(resp.text)
        title = doc.short_title() or None
        summary_html = doc.summary()
        stripper = _TextStripper()
        stripper.feed(summary_html)
        text = stripper.get_text()[:max_chars]
    except Exception:  # noqa: BLE001
        # readability can fail on JS-heavy or malformed pages; fall back
        # to a stripped-from-original-HTML body.
        stripper = _TextStripper()
        stripper.feed(resp.text)
        text = stripper.get_text()[:max_chars]

    links = _extract_links(resp.text, base=url, limit=50)
    return {
        "url": url,
        "status": resp.status_code,
        "title": title,
        "text": text,
        "links": links,
    }


# ── web_search ───────────────────────────────────────────────────


async def web_search(payload: dict[str, Any]) -> dict[str, Any]:
    """Search the web via DuckDuckGo (no API key required)."""
    query = payload["query"]
    max_results = int(payload.get("max_results", 10))
    try:
        with DDGS() as ddgs:
            hits = ddgs.text(query, max_results=max_results) or []
    except Exception as exc:  # noqa: BLE001
        if "ratelimit" in str(exc).lower() or "rate" in str(exc).lower():
            return {"results": [], "rate_limited": True}
        raise
    results = [
        {"title": h.get("title", ""), "url": h.get("href", ""), "snippet": h.get("body", "")}
        for h in hits
    ]
    return {"results": results}


# ── pdf_parse ────────────────────────────────────────────────────


async def pdf_parse(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract text from a PDF given a URL or base64-encoded bytes."""
    url = payload.get("url")
    bytes_b64 = payload.get("bytes_b64")
    if bool(url) == bool(bytes_b64):
        raise ValueError("pdf_parse requires exactly one of `url` or `bytes_b64`")
    max_pages = int(payload.get("max_pages", 20))

    if url:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA},
            ) as client:
                resp = await client.get(url)
            raw = resp.content
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}", "url": url}
    else:
        raw = base64.b64decode(bytes_b64)

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    page_count = len(reader.pages)
    truncated = page_count > max_pages
    page_texts: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            page_texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            page_texts.append("")
    text = "\n\n----\n\n".join(p for p in page_texts if p)
    return {"pages": page_count, "text": text, "truncated": truncated}
