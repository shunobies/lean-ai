"""Web search and URL fetching with HTML sanitization.

Supports two search backends:
- duckduckgo (default) — uses duckduckgo-search package
- searxng — queries a self-hosted SearXNG instance

Sanitization: HTML strip via BeautifulSoup, then optional LLM summary
for long content. No regex injection detection — trust the prompt setup.
"""

from __future__ import annotations

import asyncio
import logging
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


_SEARCH_PROVIDERS = {
    "duckduckgo": _search_duckduckgo,
    "searxng": _search_searxng,
}


# ── Public API ──


async def search_internet(
    query: str, llm_client: LLMClient | None = None,
) -> ToolResult:
    """Search the web and return sanitized results."""
    provider_fn = _SEARCH_PROVIDERS.get(settings.search_provider)
    if provider_fn is None:
        return ToolResult(
            success=False,
            error=f"Unknown search provider: '{settings.search_provider}'",
        )

    try:
        raw_content = await provider_fn(query)
    except Exception as e:
        return ToolResult(success=False, error=f"Search failed: {e}")

    sanitized = _strip_html(raw_content)

    if llm_client is not None:
        sanitized = await _summarize_if_long(sanitized, llm_client)

    return ToolResult(success=True, output=sanitized)


async def fetch_url(
    url: str,
    llm_client: LLMClient | None = None,
    max_content_bytes: int = 500_000,
) -> ToolResult:
    """Fetch a URL and return sanitized content."""
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
        return ToolResult(success=False, error=f"Timeout fetching: {url}")
    except httpx.HTTPStatusError as e:
        return ToolResult(success=False, error=f"HTTP {e.response.status_code}: {url}")
    except Exception as e:
        return ToolResult(success=False, error=f"Failed to fetch: {e}")

    sanitized = _strip_html(raw_content)

    if llm_client is not None:
        sanitized = await _summarize_if_long(sanitized, llm_client)

    return ToolResult(success=True, output=sanitized)
