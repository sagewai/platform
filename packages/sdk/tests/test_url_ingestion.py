# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for URL ingestion (#406)."""

import pytest

from sagewai.context.url_parser import (
    _extract_html_metadata,
    _html_to_text,
    _is_private_ip,
    _validate_url,
)


class TestUrlValidation:
    def test_valid_https(self):
        assert _validate_url("https://example.com") == "https://example.com"

    def test_valid_http(self):
        assert _validate_url("http://example.com") == "http://example.com"

    def test_rejects_ftp(self):
        with pytest.raises(ValueError, match="Invalid URL scheme"):
            _validate_url("ftp://example.com")

    def test_rejects_no_host(self):
        with pytest.raises(ValueError, match="no host"):
            _validate_url("https://")

    def test_blocks_localhost(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_url("https://127.0.0.1/secret")

    def test_blocks_private_10(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_url("https://10.0.0.1/admin")

    def test_blocks_private_192(self):
        with pytest.raises(ValueError, match="SSRF"):
            _validate_url("https://192.168.1.1/config")

    def test_allows_public_ip(self):
        assert _validate_url("https://8.8.8.8") == "https://8.8.8.8"


class TestPrivateIpDetection:
    def test_localhost(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_private_10(self):
        assert _is_private_ip("10.0.0.1") is True

    def test_public(self):
        assert _is_private_ip("8.8.8.8") is False

    def test_domain_allowed(self):
        # Domains (not IPs) are allowed through
        assert _is_private_ip("example.com") is False


class TestHtmlToText:
    def test_basic_html(self):
        html = "<html><body><p>Hello world</p></body></html>"
        text = _html_to_text(html)
        assert "Hello world" in text

    def test_strips_scripts(self):
        html = "<script>alert('xss')</script><p>Content</p>"
        text = _html_to_text(html)
        assert "alert" not in text
        assert "Content" in text

    def test_strips_nav_footer(self):
        html = "<nav>Menu items</nav><main><p>Article content</p></main><footer>Copyright</footer>"
        text = _html_to_text(html)
        assert "Menu items" not in text
        assert "Article content" in text
        assert "Copyright" not in text

    def test_preserves_link_text(self):
        html = '<a href="https://example.com">Click here</a>'
        text = _html_to_text(html)
        assert "Click here" in text

    def test_decodes_entities(self):
        html = "<p>Hello &amp; goodbye &lt;world&gt;</p>"
        text = _html_to_text(html)
        assert "&" in text
        assert "<world>" in text


class TestHtmlMetadata:
    def test_extracts_title(self):
        html = "<html><head><title>My Page</title></head></html>"
        meta = _extract_html_metadata(html, "https://example.com")
        assert meta["title"] == "My Page"

    def test_extracts_description(self):
        html = '<meta name="description" content="A cool page">'
        meta = _extract_html_metadata(html, "https://example.com")
        assert meta["description"] == "A cool page"

    def test_extracts_canonical(self):
        html = '<link rel="canonical" href="https://example.com/page">'
        meta = _extract_html_metadata(html, "https://example.com/page?ref=1")
        assert meta["canonical_url"] == "https://example.com/page"

    def test_missing_metadata(self):
        html = "<html><body>No meta</body></html>"
        meta = _extract_html_metadata(html, "https://example.com")
        assert meta["url"] == "https://example.com"
        assert "title" not in meta
