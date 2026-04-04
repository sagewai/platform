# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""URL fetching and parsing for context ingestion.

Fetches web pages via httpx, converts HTML to clean text, and extracts
metadata (title, description, canonical URL).
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any
from urllib.parse import urlparse

from sagewai.context.models import ParsedDocument

logger = logging.getLogger(__name__)

# Block private/internal IPs to prevent SSRF
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10MB


def _is_private_ip(hostname: str) -> bool:
    """Check if hostname or its resolved IPs are private/internal."""
    import socket

    # First check if hostname is literally an IP address
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_RANGES)
    except ValueError:
        pass

    # Resolve domain to IP addresses and check each
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in results:
            ip_str = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip_str)
                if any(addr in net for net in _PRIVATE_RANGES):
                    return True
            except ValueError:
                continue
    except socket.gaierror:
        # DNS resolution failed — block by default for safety
        return True

    return False


def _validate_url(url: str) -> str:
    """Validate and normalize a URL. Raises ValueError if invalid."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}. Only http/https allowed.")
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: no host in {url}")
    if _is_private_ip(parsed.hostname or ""):
        raise ValueError(f"SSRF blocked: private/internal IP for {parsed.hostname}")
    return url


def _resolve_and_pin_url(url: str) -> tuple[str, str]:
    """Resolve hostname to IP and return (pinned_url, original_host).

    Prevents DNS rebinding by pinning the resolved IP for the actual
    request, while preserving the original Host header for TLS/vhosts.
    """
    import socket

    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Resolve to IP
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not results:
            raise ValueError(f"DNS resolution returned no results for {hostname}")
        ip_str = results[0][4][0]
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for {hostname}: {e}") from e

    # Validate resolved IP is not private
    addr = ipaddress.ip_address(ip_str)
    if any(addr in net for net in _PRIVATE_RANGES):
        raise ValueError(f"SSRF blocked: {hostname} resolves to private IP {ip_str}")

    # Rewrite URL to use resolved IP (preserving port and path)
    port_suffix = f":{parsed.port}" if parsed.port else ""
    pinned_url = parsed._replace(netloc=f"{ip_str}{port_suffix}").geturl()
    return pinned_url, hostname


async def fetch_and_parse(
    url: str,
    timeout: float = 30.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> ParsedDocument:
    """Fetch a URL and parse it into a ParsedDocument.

    Handles HTML pages (converted to clean text) and binary content
    (delegated to Docling if available).

    Parameters
    ----------
    url:
        The URL to fetch. Must be http:// or https://.
    timeout:
        Request timeout in seconds.
    max_bytes:
        Maximum response body size.

    Raises
    ------
    ValueError:
        If the URL is invalid or points to a private IP.
    httpx.HTTPStatusError:
        If the server returns an error status.
    """
    import httpx

    validated_url = _validate_url(url)

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=timeout,
        headers={"User-Agent": "Sagewai-Bot/1.0"},
    ) as client:
        # Follow redirects manually with HEAD to avoid downloading bodies.
        # Validate each hop against private IP ranges.
        current_url = validated_url
        for _ in range(10):  # max 10 redirects
            response = await client.head(current_url)
            if response.is_redirect:
                redirect_url = str(response.next_request.url) if response.next_request else None
                if not redirect_url:
                    raise ValueError("Redirect without Location header")
                # Validate redirect target against private IP ranges
                _validate_url(redirect_url)
                current_url = redirect_url
                continue
            break
        else:
            raise ValueError("Too many redirects (max 10)")

        # Pin DNS resolution to prevent rebinding between validation and fetch.
        # The resolved IP is used for the actual request; the original hostname
        # is sent as the Host header for TLS SNI and virtual hosting.
        pinned_url, original_host = _resolve_and_pin_url(current_url)

        async with client.stream(
            "GET",
            pinned_url,
            headers={"Host": original_host},
        ) as stream:
            stream.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in stream.aiter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"Response too large: >{max_bytes} bytes (limit {max_bytes})"
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            content_type = stream.headers.get("content-type", "")
            response_text = content.decode("utf-8", errors="replace")

    if "html" in content_type:
        text = _html_to_text(response_text)
        metadata = _extract_html_metadata(response_text, url)
        return ParsedDocument(text=text, metadata=metadata, mime_type="text/html")

    elif "pdf" in content_type:
        from sagewai.context.parsers import parse_document

        return await parse_document(content, "application/pdf", filename=url)

    else:
        return ParsedDocument(
            text=response_text,
            metadata={"url": url, "content_type": content_type},
            mime_type=content_type or "text/plain",
        )


def _html_to_text(html: str) -> str:
    """Convert HTML to clean plain text.

    Strips scripts, styles, nav, footer, and converts basic tags to text.
    """
    # Remove script, style, nav, footer, header, aside
    for tag in ["script", "style", "nav", "footer", "header", "aside", "noscript"]:
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE
        )

    # Convert common block elements to newlines
    for tag in ["p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"]:
        html = re.sub(rf"</?{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)

    # Convert links to text
    html = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", html, flags=re.DOTALL | re.IGNORECASE)

    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", " ", html)

    # Decode common HTML entities
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&quot;", '"')

    # Normalize whitespace
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r" +", " ", text)

    return text.strip()


def _extract_html_metadata(html: str, url: str) -> dict[str, Any]:
    """Extract title, description, and canonical URL from HTML."""
    metadata: dict[str, Any] = {"url": url, "parser": "url_parser"}

    # Title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if title_match:
        metadata["title"] = title_match.group(1).strip()

    # Meta description
    desc_match = re.search(
        r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if desc_match:
        metadata["description"] = desc_match.group(1).strip()

    # Canonical URL
    canonical_match = re.search(
        r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if canonical_match:
        metadata["canonical_url"] = canonical_match.group(1).strip()

    return metadata
