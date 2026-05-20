# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for sagewai.tools.builtins.http_parsing."""
import base64

import pytest
import respx

from sagewai.tools.builtins import http_parsing as hp


# ── fetch_url ────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_fetch_url_returns_truncated_body():
    respx.get("https://example.test/").respond(200, text="A" * 10_000)
    out = await hp.fetch_url({"url": "https://example.test/"})
    assert out["status"] == 200
    assert out["url"] == "https://example.test/"
    assert len(out["body"]) <= 8_000


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme():
    out = await hp.fetch_url({"url": "ftp://example.test/"})
    assert "error" in out


# ── web_scrape ───────────────────────────────────────────────────

_PAGE = b"""
<html><head><title>Example</title></head>
<body><article><h1>Heading</h1><p>Main content paragraph one.</p>
<p>Main content paragraph two.</p></article>
<a href="/about">About</a> <a href="https://other.test/x">Other</a></body></html>
"""


@pytest.mark.asyncio
@respx.mock
async def test_web_scrape_extracts_text_title_and_links():
    respx.get("https://example.test/").respond(200, content=_PAGE)
    out = await hp.web_scrape({"url": "https://example.test/"})
    assert out["status"] == 200
    assert "Main content paragraph" in out["text"]
    assert out["title"] is not None
    assert any("about" in link.lower() for link in out["links"])


@pytest.mark.asyncio
@respx.mock
async def test_web_scrape_truncates_to_max_chars():
    big_body = b"<html><body><p>" + (b"x" * 20_000) + b"</p></body></html>"
    respx.get("https://example.test/big").respond(200, content=big_body)
    out = await hp.web_scrape({"url": "https://example.test/big", "max_chars": 500})
    assert len(out["text"]) <= 500


# ── web_search ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_returns_results(monkeypatch):
    fake_hits = [
        {"title": "Sagewai", "href": "https://sagewai.ai/", "body": "Open agent platform"},
        {"title": "Docs", "href": "https://docs.sagewai.ai/", "body": "Documentation"},
    ]

    class FakeDDGS:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def text(self, query, max_results):
            return fake_hits[:max_results]

    monkeypatch.setattr(hp, "DDGS", FakeDDGS)
    out = await hp.web_search({"query": "sagewai", "max_results": 5})
    assert len(out["results"]) == 2
    assert out["results"][0]["url"] == "https://sagewai.ai/"
    assert out["results"][0]["title"] == "Sagewai"
    assert out["results"][0]["snippet"] == "Open agent platform"


@pytest.mark.asyncio
async def test_web_search_returns_empty_on_rate_limit(monkeypatch):
    class RateLimitedDDGS:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def text(self, *a, **kw):
            raise RuntimeError("Ratelimit")

    monkeypatch.setattr(hp, "DDGS", RateLimitedDDGS)
    out = await hp.web_search({"query": "x"})
    assert out["results"] == []
    assert out.get("rate_limited") is True


# ── pdf_parse ────────────────────────────────────────────────────

def _make_one_page_pdf_bytes(text: str) -> bytes:
    """Hand-rolled minimal one-page PDF."""
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length " + str(len(text) + 50).encode() + b" >>\n"
        b"stream\nBT /F1 12 Tf 50 700 Td (" + text.encode() + b") Tj ET\nendstream endobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f\n"
        b"0000000010 00000 n\n0000000058 00000 n\n0000000110 00000 n\n"
        b"0000000220 00000 n\n0000000350 00000 n\n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n420\n%%EOF\n"
    )
    return pdf


@pytest.mark.asyncio
async def test_pdf_parse_requires_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        await hp.pdf_parse({})
    with pytest.raises(ValueError, match="exactly one"):
        await hp.pdf_parse({
            "url": "https://example.test/x.pdf",
            "bytes_b64": base64.b64encode(b"x").decode(),
        })


@pytest.mark.asyncio
async def test_pdf_parse_from_bytes_b64():
    pdf_bytes = _make_one_page_pdf_bytes("Hello PDF")
    out = await hp.pdf_parse({"bytes_b64": base64.b64encode(pdf_bytes).decode()})
    assert out["pages"] >= 1
    assert isinstance(out["text"], str)
    assert isinstance(out["truncated"], bool)


@pytest.mark.asyncio
@respx.mock
async def test_pdf_parse_from_url():
    pdf_bytes = _make_one_page_pdf_bytes("Hi")
    respx.get("https://example.test/doc.pdf").respond(
        200, content=pdf_bytes, headers={"content-type": "application/pdf"},
    )
    out = await hp.pdf_parse({"url": "https://example.test/doc.pdf"})
    assert out["pages"] >= 1
