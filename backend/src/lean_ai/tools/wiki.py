"""MediaWiki search and page fetching via the Action API.

Provides two tools:
- search_wiki: full-text search across a MediaWiki instance
- fetch_wiki_page: retrieve full page content by title

Supports both authenticated (bot account / user login) and
unauthenticated (public) wikis. Authentication is lazy — login
happens on the first request when credentials are configured.

Uses the same HTML stripping and pagination patterns as internet.py.
"""

from __future__ import annotations

import logging

import httpx

from lean_ai.config import settings
from lean_ai.tools.executor import ToolResult
from lean_ai.tools.html_utils import save_and_paginate, strip_html

logger = logging.getLogger(__name__)

# Module-level client — keeps session cookies for authenticated wikis.
_client: httpx.AsyncClient | None = None
_authenticated: bool = False


def _api_url() -> str:
    """Build the full MediaWiki API URL from config."""
    base = settings.wiki_url.rstrip("/")
    path = settings.wiki_api_path
    return f"{base}{path}"


async def _get_client() -> httpx.AsyncClient:
    """Return (and lazily create) the shared HTTP client."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "LeanAI/1.0 (MediaWiki tool)"},
        )
    return _client


async def _ensure_authenticated() -> None:
    """Log in to the wiki if credentials are configured and we haven't yet.

    Uses the MediaWiki ``action=login`` two-step flow:
    1. Fetch a login token via ``action=query&meta=tokens&type=login``
    2. POST ``action=login`` with username, password, and token.

    Skips silently when no credentials are configured.
    """
    global _authenticated
    if _authenticated:
        return
    if not settings.wiki_username or not settings.wiki_password:
        _authenticated = True  # No credentials → nothing to do
        return

    client = await _get_client()
    api = _api_url()

    try:
        # Step 1: get login token
        token_resp = await client.get(
            api,
            params={
                "action": "query",
                "meta": "tokens",
                "type": "login",
                "format": "json",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        login_token = token_data["query"]["tokens"]["logintoken"]

        # Step 2: login
        login_resp = await client.post(
            api,
            data={
                "action": "login",
                "lgname": settings.wiki_username,
                "lgpassword": settings.wiki_password,
                "lgtoken": login_token,
                "format": "json",
            },
        )
        login_resp.raise_for_status()
        login_result = login_resp.json().get("login", {})

        if login_result.get("result") == "Success":
            logger.info("MediaWiki login successful for user %s", settings.wiki_username)
            _authenticated = True
        else:
            reason = login_result.get("reason", login_result.get("result", "Unknown"))
            logger.warning("MediaWiki login failed: %s", reason)
            # Mark as authenticated to avoid retrying every request
            _authenticated = True
    except Exception as e:
        logger.warning("MediaWiki authentication error: %s", e)
        _authenticated = True  # Don't retry on every request


async def search_wiki(query: str, limit: int = 5) -> ToolResult:
    """Search MediaWiki for pages matching the query.

    Uses ``action=query&list=search`` to perform a full-text search.
    Returns formatted results with page titles, snippets, and URLs.
    """
    if not settings.wiki_url:
        return ToolResult(
            success=False,
            error="MediaWiki not configured (LEAN_AI_WIKI_URL is empty)",
        )

    if not query.strip():
        return ToolResult(success=False, error="Search query cannot be empty")

    await _ensure_authenticated()
    client = await _get_client()
    api = _api_url()

    try:
        resp = await client.get(
            api,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": str(limit),
                "srprop": "snippet|titlesnippet|size|wordcount",
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        return ToolResult(success=False, error="MediaWiki search timed out")
    except httpx.HTTPStatusError as e:
        return ToolResult(success=False, error=f"MediaWiki HTTP error: {e.response.status_code}")
    except Exception as e:
        return ToolResult(success=False, error=f"MediaWiki search failed: {e}")

    results = data.get("query", {}).get("search", [])
    if not results:
        return ToolResult(success=True, output=f"No wiki pages found for: {query}")

    base_url = settings.wiki_url.rstrip("/")
    lines: list[str] = []
    for item in results:
        title = item.get("title", "")
        snippet = strip_html(item.get("snippet", ""))
        word_count = item.get("wordcount", 0)
        page_url = f"{base_url}/wiki/{title.replace(' ', '_')}"
        lines.append(f"Title: {title}\nURL: {page_url}\nWords: {word_count}\n{snippet}\n")

    return ToolResult(success=True, output="\n".join(lines))


async def fetch_wiki_page(title: str, repo_root: str) -> ToolResult:
    """Fetch the full content of a wiki page by title.

    Uses ``action=parse`` to get the rendered HTML, then strips it
    to plain text.  Long pages are saved to disk with pagination.
    """
    if not settings.wiki_url:
        return ToolResult(
            success=False,
            error="MediaWiki not configured (LEAN_AI_WIKI_URL is empty)",
        )

    if not title.strip():
        return ToolResult(success=False, error="Page title cannot be empty")

    await _ensure_authenticated()
    client = await _get_client()
    api = _api_url()

    try:
        resp = await client.get(
            api,
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        return ToolResult(success=False, error=f"Fetching wiki page '{title}' timed out")
    except httpx.HTTPStatusError as e:
        return ToolResult(success=False, error=f"MediaWiki HTTP error: {e.response.status_code}")
    except Exception as e:
        return ToolResult(success=False, error=f"Failed to fetch wiki page: {e}")

    # Check for API-level error (e.g. page not found)
    if "error" in data:
        error_info = data["error"].get("info", "Unknown error")
        return ToolResult(success=False, error=f"MediaWiki error: {error_info}")

    html = data.get("parse", {}).get("text", {}).get("*", "")
    if not html:
        return ToolResult(success=False, error=f"Wiki page '{title}' has no content")

    text = strip_html(html)

    # Add page header
    base_url = settings.wiki_url.rstrip("/")
    page_url = f"{base_url}/wiki/{title.replace(' ', '_')}"
    header = f"# {title}\nSource: {page_url}\n\n"
    full_text = header + text

    # Paginate long pages
    output = save_and_paginate(full_text, f"wiki:{title}", repo_root)
    return ToolResult(success=True, output=output)


async def close_wiki_client() -> None:
    """Close the shared HTTP client. Call on shutdown."""
    global _client, _authenticated
    if _client is not None:
        await _client.aclose()
        _client = None
    _authenticated = False
