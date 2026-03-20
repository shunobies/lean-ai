"""Web search and URL fetching with HTML sanitization.

Supports four search backends:
- duckduckgo (default) — uses duckduckgo-search package
- searxng — queries a self-hosted SearXNG instance
- google — headless Chrome via Selenium (auto-falls back to Bing on failure)
- bing — standalone headless Chrome via Selenium

Sanitization: HTML strip via BeautifulSoup, then optional LLM summary
for long content. No regex injection detection — trust the prompt setup.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult

if TYPE_CHECKING:
    from lean_ai.llm.client import LLMClient

logger = logging.getLogger(__name__)


def _strip_html(raw: str) -> str:
    """Remove HTML tags, extract text content."""
    soup = BeautifulSoup(raw, "html.parser")
    # Remove script and style elements
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


async def _summarize_if_long(
    text: str, llm_client: LLMClient, threshold: int = 3000,
) -> str:
    """Use LLM to summarize content exceeding threshold."""
    if len(text) <= threshold:
        return text

    try:
        summary = await llm_client.chat_raw(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the following web content concisely. "
                        "Focus on technical facts, code examples, and actionable information. "
                        "Preserve URLs, version numbers, and code snippets."
                    ),
                },
                {"role": "user", "content": text[:20000]},  # Cap input
            ],
            max_tokens=1024,
        )
        return summary
    except Exception as e:
        logger.warning("LLM summarization failed, returning truncated: %s", e)
        return text[:threshold] + "\n\n[Content truncated]"


def _save_and_paginate(
    text: str, url: str, repo_root: str, max_lines: int = 500,
) -> str:
    """Save long content to a file and return the first page with a continuation hint.

    Short content (<= max_lines) is returned as-is.  Long content is
    written to ``.lean_ai/fetched/{hash}.txt`` and only the first
    *max_lines* lines are returned, with an instruction telling the
    LLM how to read the rest via ``read_file``.
    """
    lines = text.splitlines()
    total = len(lines)

    if total <= max_lines:
        return text

    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    fetched_dir = Path(repo_root) / ".lean_ai" / "fetched"
    fetched_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f".lean_ai/fetched/{url_hash}.txt"
    file_path = Path(repo_root) / rel_path
    file_path.write_text(text, encoding="utf-8")

    preview = "\n".join(lines[:max_lines])
    next_start = max_lines + 1
    next_end = min(max_lines + 500, total)
    return (
        f"{preview}\n\n"
        f"[Showing lines 1-{max_lines} of {total}. "
        f"Remaining content saved to {rel_path}.\n"
        f"To continue reading, call: "
        f'read_file path="{rel_path}" start_line={next_start} end_line={next_end}]'
    )


# ── Search providers ──


async def _search_duckduckgo(query: str, max_results: int = 5) -> str:
    """Search via duckduckgo-search package."""
    from duckduckgo_search import DDGS

    def _do_search() -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    results = await asyncio.to_thread(_do_search)

    if not results:
        return f"No results found for: {query}"

    parts: list[str] = []
    for r in results:
        title = r.get("title", "")
        href = r.get("href", "")
        body = r.get("body", "")
        parts.append(f"Title: {title}\nURL: {href}\n{body}")

    return "\n\n---\n\n".join(parts)


async def _search_searxng(query: str, max_results: int = 5) -> str:
    """Search via self-hosted SearXNG JSON API."""
    if not settings.search_api_url:
        raise RuntimeError("SearXNG search_api_url is not configured.")

    base_url = settings.search_api_url.rstrip("/")
    params = {"q": query, "format": "json"}
    headers: dict[str, str] = {}
    if settings.search_api_key:
        headers["Authorization"] = f"Bearer {settings.search_api_key}"

    async with httpx.AsyncClient(
        timeout=settings.internet_timeout_seconds, follow_redirects=True,
    ) as client:
        response = await client.get(f"{base_url}/search", params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

    results = data.get("results", [])[:max_results]
    if not results:
        return f"No results found for: {query}"

    parts: list[str] = []
    for r in results:
        title = r.get("title", "")
        url = r.get("url", "")
        content = r.get("content", "")
        parts.append(f"Title: {title}\nURL: {url}\n{content}")
    return "\n\n---\n\n".join(parts)


async def _search_google(query: str, max_results: int = 10) -> str:
    """Search via headless Chrome + Google.  Requires ``selenium``."""
    from lean_ai.tools.browser_search import search_google

    return await search_google(query, max_results)


async def _search_bing(query: str, max_results: int = 10) -> str:
    """Search via headless Chrome + Bing.  Requires ``selenium``."""
    from lean_ai.tools.browser_search import search_bing

    return await search_bing(query, max_results)


_SEARCH_PROVIDERS = {
    "duckduckgo": _search_duckduckgo,
    "searxng": _search_searxng,
    "google": _search_google,
    "bing": _search_bing,
}


# ── Rate limiting ──

_last_search_time: float = 0.0


async def _enforce_search_delay() -> None:
    """Wait if needed to respect the configured delay between searches.

    Measures time since the last search *completed* (not started) to
    ensure a real gap between requests.  Uses ``settings.search_delay``
    with random jitter (0–100 % of base delay).

    Browser-based providers (Google, Bing) automatically use a 4×
    multiplier (floor 8 s) because headless Chrome triggers anti-bot
    detection much faster than API-based providers.
    """
    base_delay = settings.search_delay
    if base_delay <= 0:
        return

    # Browser-based providers (headless Chrome) trigger anti-bot
    # detection much faster than API calls.  Apply a 4× multiplier
    # with a floor of 8 s so Google/Bing get real breathing room.
    if settings.search_provider in ("google", "bing"):
        base_delay = max(base_delay * 4, 8.0)

    elapsed = time.monotonic() - _last_search_time
    target_delay = base_delay + random.uniform(0, base_delay)
    if elapsed < target_delay:
        wait = target_delay - elapsed
        logger.debug("Search rate-limit: sleeping %.1fs", wait)
        await asyncio.sleep(wait)


# ── Public API ──


async def search_internet(
    query: str, llm_client: LLMClient | None = None,
) -> ToolResult:
    """Search the web and return sanitized results."""
    global _last_search_time

    provider_fn = _SEARCH_PROVIDERS.get(settings.search_provider)
    if provider_fn is None:
        return ToolResult(
            success=False,
            error=f"Unknown search provider: '{settings.search_provider}'",
        )

    logger.info("Search [%s]: %r", settings.search_provider, query)

    # Universal rate limiting — all providers respect search_delay.
    # The delay is measured from when the PREVIOUS search completed,
    # not when it started — so browser searches that take 10-30 s
    # don't eat into the delay window.
    await _enforce_search_delay()

    try:
        raw_content = await provider_fn(query)
    except Exception as e:
        _last_search_time = time.monotonic()
        logger.warning(
            "Search [%s]: %r failed: %s", settings.search_provider, query, e,
        )
        return ToolResult(success=False, error=f"Search failed: {e}")

    # Record completion time AFTER the search finishes so the next
    # delay is measured from end-to-start, not start-to-start.
    _last_search_time = time.monotonic()

    sanitized = _strip_html(raw_content)

    if llm_client is not None:
        sanitized = await _summarize_if_long(sanitized, llm_client)

    logger.info(
        "Search [%s]: %r -> %d chars",
        settings.search_provider, query, len(sanitized),
    )
    return ToolResult(success=True, output=sanitized)


async def fetch_url(
    url: str,
    repo_root: str = "",
    llm_client: LLMClient | None = None,
    max_content_bytes: int = 500_000,
) -> ToolResult:
    """Fetch a URL and return sanitized content.

    Long pages (> 500 lines) are saved to ``.lean_ai/fetched/`` and
    only the first 500 lines are returned, with a ``read_file``
    instruction for the remainder.
    """
    logger.info("Fetch URL: %s", url)
    try:
        async with httpx.AsyncClient(
            timeout=settings.internet_timeout_seconds, follow_redirects=True,
        ) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; LeanAI/1.0)",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
            response.raise_for_status()
            raw_bytes = response.content[:max_content_bytes]
            encoding = response.encoding or "utf-8"
            raw_content = raw_bytes.decode(encoding, errors="replace")
    except httpx.TimeoutException:
        logger.warning("Fetch URL: timeout: %s", url)
        return ToolResult(success=False, error=f"Timeout fetching: {url}")
    except httpx.HTTPStatusError as e:
        logger.warning("Fetch URL: HTTP %d: %s", e.response.status_code, url)
        return ToolResult(success=False, error=f"HTTP {e.response.status_code}: {url}")
    except Exception as e:
        logger.warning("Fetch URL: failed: %s — %s", url, e)
        return ToolResult(success=False, error=f"Failed to fetch: {e}")

    sanitized = _strip_html(raw_content)

    # Save long content to file for paginated reading via read_file
    if repo_root:
        sanitized = _save_and_paginate(sanitized, url, repo_root)

    logger.info("Fetch URL: %s -> %d chars", url, len(sanitized))
    return ToolResult(success=True, output=sanitized)
