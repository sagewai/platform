# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Default tool registry for autopilot missions.

Provides minimal real implementations for the small set of tools the
synthesis prompt and the seed corpus reference. Each tool is best-effort
and graceful: failures return a structured error string rather than
raising, so the LLM can read the result and recover.

The registry is built lazily once per process. Add a new tool by
registering it on the singleton; the executor will pass its spec to
LiteLLM the next time a mission step references it.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from typing import Any

from sagewai.autopilot.controller.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_FETCH_LIMIT = 8000  # truncate fetched body to keep prompt budgets sane
_USER_AGENT = "Mozilla/5.0 (Sagewai Autopilot)"

# Phrases that reliably appear on consent / cookie / login walls in
# multiple languages. We don't try to be exhaustive — three or four
# matches is enough to flag the page so the LLM stops using it as
# content.
_CONSENT_WALL_MARKERS = (
    "before you continue to google",
    "bevor sie zu google",
    "we use cookies and data",
    "wir verwenden cookies und daten",
    "accept all cookies",
    "alle cookies akzeptieren",
    "consent.google",
    "captcha",
    "are you a robot",
    "ich bin kein roboter",
    "please verify you are human",
)


def _looks_like_consent_wall(text: str) -> bool:
    """True when *text* matches the rough signature of a consent/CAPTCHA wall."""
    if not text:
        return False
    sample = text[:4000].lower()
    hits = sum(1 for marker in _CONSENT_WALL_MARKERS if marker in sample)
    return hits >= 2


async def _fetch_url(url: str, max_chars: int = _FETCH_LIMIT) -> str:
    """Fetch *url* and return up to *max_chars* of stripped text body."""
    try:
        import httpx
    except ModuleNotFoundError:
        return "[fetch_url unavailable: httpx not installed]"
    if not url or not url.startswith(("http://", "https://")):
        return (
            f"[fetch_url error: invalid url {url!r} — must start with http:// "
            "or https://. Try web_search first to discover real URLs.]"
        )
    # Split timeouts: a short connect window (so an unreachable host
    # fails fast) and a longer read window (slow servers still succeed).
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text or ""
    except Exception as exc:  # noqa: BLE001
        # Always include the URL — small models otherwise can't tell
        # which of several recent calls failed and retry the same one.
        return (
            f"[fetch_url error: {type(exc).__name__}: {exc} "
            f"(url={url!r}). Try a different URL or call web_search.]"
        )
    # Strip script/style blocks then collapse tags so the LLM sees readable text.
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if _looks_like_consent_wall(text):
        return (
            f"[fetch_url returned a consent / CAPTCHA wall for {url!r}. "
            "Don't pass search-engine homepages or login pages to fetch_url; "
            "call web_search to discover canonical article URLs, or read_rss "
            "for a news feed, then fetch_url the actual article URL.]"
        )
    return text[:max_chars]


_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


async def _web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return up to *max_results* hits as plain text.

    Backend ladder, in priority order:

    1. **Serper** (``SERPER_API_KEY``) — Google results via serper.dev.
       Free tier covers ~2.5K queries/month. Most reliable for production.
    2. **Tavily** (``TAVILY_API_KEY``) — agent-tuned search with
       LLM-friendly snippets. Free tier ~1K/month.
    3. **Brave** (``BRAVE_SEARCH_API_KEY``) — independent index, good
       coverage. Free tier 2K/month.
    4. **DuckDuckGo HTML** — unauthenticated, rate-limited fallback.
       Brittle: regional consent walls, occasional captchas, no SLA.

    Output is the same shape regardless of backend so the LLM doesn't
    need to know which one ran: ``Title — URL\\nSnippet`` blocks
    separated by blank lines.
    """
    try:
        import httpx
    except ModuleNotFoundError:
        return "[web_search unavailable: httpx not installed]"

    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)

    serper_key = os.environ.get("SERPER_API_KEY")
    if serper_key:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, headers={"X-API-KEY": serper_key, "Content-Type": "application/json"}
            ) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    json={"q": query, "num": max_results},
                )
                resp.raise_for_status()
                data = resp.json()
            hits = data.get("organic") or []
            if hits:
                return "\n\n".join(
                    f"{h.get('title','')} — {h.get('link','')}\n{h.get('snippet','')}"
                    for h in hits[:max_results]
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_search: Serper failed (%s) — falling through", exc)

    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            hits = data.get("results") or []
            if hits:
                return "\n\n".join(
                    f"{h.get('title','')} — {h.get('url','')}\n{h.get('content','')}"
                    for h in hits[:max_results]
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_search: Tavily failed (%s) — falling through", exc)

    brave_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if brave_key:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
            ) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": max_results},
                )
                resp.raise_for_status()
                data = resp.json()
            hits = (data.get("web") or {}).get("results") or []
            if hits:
                return "\n\n".join(
                    f"{h.get('title','')} — {h.get('url','')}\n{h.get('description','')}"
                    for h in hits[:max_results]
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_search: Brave failed (%s) — falling through", exc)

    # DuckDuckGo HTML — last resort.
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:  # noqa: BLE001
        return (
            f"[web_search error: {type(exc).__name__}: {exc} "
            f"(query={query!r}). For reliable search, configure "
            "SERPER_API_KEY, TAVILY_API_KEY, or BRAVE_SEARCH_API_KEY.]"
        )

    matches = _DDG_RESULT_RE.findall(body)
    if not matches:
        return (
            "[web_search returned no parseable results — DuckDuckGo "
            "rate-limited or changed its HTML. For reliable search, set "
            "SERPER_API_KEY, TAVILY_API_KEY, or BRAVE_SEARCH_API_KEY in "
            "the platform process environment.]"
        )

    def _strip(s: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?is)<[^>]+>", "", s))).strip()

    return "\n\n".join(
        f"{_strip(title)} — {url}\n{_strip(snippet)}"
        for url, title, snippet in matches[:max_results]
    )


async def _read_rss(url: str, max_items: int = 10) -> str:
    """Fetch an RSS / Atom feed and return up to *max_items* entries.

    Designed for news / blog goals where the source publishes a feed —
    no consent wall, no JS rendering, and the result is structured. If
    you want news and the source has /feed, /rss, or /atom.xml,
    prefer this over web_search → fetch_url.
    """
    try:
        import httpx
    except ModuleNotFoundError:
        return "[read_rss unavailable: httpx not installed]"
    if not url or not url.startswith(("http://", "https://")):
        return f"[read_rss error: invalid url {url!r} — must start with http(s)://]"
    timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.text or ""
    except Exception as exc:  # noqa: BLE001
        return f"[read_rss error: {type(exc).__name__}: {exc} (url={url!r})]"

    # Permissive parser — handles both RSS 2.0 (<item>) and Atom (<entry>).
    item_re = re.compile(r"(?is)<(item|entry)\b[^>]*>(.*?)</\1>")
    title_re = re.compile(r"(?is)<title[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>")
    link_re = re.compile(
        r"""(?is)<link[^>]*?(?:href\s*=\s*["']([^"']+)["']|>([^<]+)</link>)"""
    )
    desc_re = re.compile(
        r"(?is)<(?:description|summary|content)[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</(?:description|summary|content)>"
    )

    out: list[str] = []
    for m in item_re.finditer(body):
        block = m.group(2)
        title = (title_re.search(block) or [None, ""])[1] if title_re.search(block) else ""
        link_match = link_re.search(block)
        link = ""
        if link_match:
            link = link_match.group(1) or link_match.group(2) or ""
        desc_match = desc_re.search(block)
        desc = desc_match.group(1) if desc_match else ""

        def _clean(s: str) -> str:
            s = html.unescape(re.sub(r"(?is)<[^>]+>", " ", s or ""))
            return re.sub(r"\s+", " ", s).strip()

        out.append(f"{_clean(title)} — {link.strip()}\n{_clean(desc)[:400]}")
        if len(out) >= max_items:
            break

    if not out:
        return f"[read_rss found no <item>/<entry> elements at {url!r} — is it really a feed?]"
    return "\n\n".join(out)


async def _no_op(**kwargs: Any) -> str:
    """Stub for tools the platform doesn't ship with a real implementation.

    Returns a clear diagnostic so the LLM can report the missing backend
    rather than fabricating output.
    """
    return (
        "[tool not implemented in this build — the autopilot OSS package "
        "ships only fetch_url and web_search. Configure the missing tool "
        "via your blueprint's tool_registry to make it callable.]"
    )


_DEFAULT_REGISTRY: ToolRegistry | None = None


def make_default_tool_registry() -> ToolRegistry:
    """Return the process-wide default :class:`ToolRegistry`.

    Currently registers two real tools — ``fetch_url`` (HTTP GET via
    httpx) and ``web_search`` (DuckDuckGo HTML) — plus stubs for the
    rest of the seed corpus's tool surface so synthesised blueprints
    referencing them at least don't crash the executor.
    """
    global _DEFAULT_REGISTRY  # noqa: PLW0603
    if _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY

    registry = ToolRegistry()
    registry.register(
        name="fetch_url",
        description=(
            "Fetch a single article/page URL via HTTP GET and return up "
            "to ~8KB of stripped text. Use AFTER web_search or read_rss "
            "discovered the URL. DO NOT pass search-engine homepages "
            "(google.com, bing.com, duckduckgo.com) — those return "
            "consent walls, not content. Returns the page text or an "
            "error string starting with '['."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute article/page URL to fetch."},
            },
            "required": ["url"],
        },
        callable_=_fetch_url,
    )
    registry.register(
        name="web_search",
        description=(
            "Search the web and return the top results as "
            "Title/URL/snippet text. Use FIRST to discover URLs relevant "
            "to the goal, THEN call fetch_url on each useful URL. "
            "Backend ladder: Serper → Tavily → Brave → DuckDuckGo HTML "
            "(set SERPER_API_KEY / TAVILY_API_KEY / BRAVE_SEARCH_API_KEY "
            "for production-grade results)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string."},
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        callable_=_web_search,
    )
    registry.register(
        name="read_rss",
        description=(
            "Fetch and parse an RSS or Atom feed, returning the latest "
            "entries as Title/URL/summary blocks. Prefer this over "
            "web_search+fetch_url when the source publishes a feed — "
            "structured output, no consent walls, much more reliable "
            "for news / blog tracking goals. Common feed paths: /feed, "
            "/rss, /atom.xml, /index.xml."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute RSS/Atom feed URL."},
                "max_items": {
                    "type": "integer",
                    "description": "Max feed entries to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["url"],
        },
        callable_=_read_rss,
    )

    # Stubs for the rest of the seed corpus's tool surface so the
    # executor doesn't KeyError when a synthesised blueprint references
    # a name that has no real implementation. The stub returns a clear
    # diagnostic so the LLM can decide to skip the tool.
    _SEED_STUBS = (
        "acknowledge_pagerduty",
        "crm_lookup_account",
        "crm_update_lead",
        "diff_text",
        "email_send_draft",
        "fetch_recent_metrics",
        "ocr",
        "pdf_parse",
        "post_to_slack",
        "run_runbook_command",
        "structured_write",
        "ticket_create",
        "ticket_route",
        "ticket_tag",
    )
    for name in _SEED_STUBS:
        registry.register(
            name=name,
            description=(
                f"Placeholder for {name!r} — the OSS build doesn't ship a "
                "real implementation. Calls return a diagnostic; configure "
                "this tool in your environment to make it functional."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": True},
            callable_=_no_op,
        )

    _DEFAULT_REGISTRY = registry
    return registry


# A non-async accessor for callers that don't have an event loop.
def get_default_tool_registry() -> ToolRegistry:
    return make_default_tool_registry()


__all__ = [
    "get_default_tool_registry",
    "make_default_tool_registry",
]
