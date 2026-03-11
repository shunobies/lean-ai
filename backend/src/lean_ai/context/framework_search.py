"""Search result parsing and URL selection for framework guide generation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def extract_urls_from_search(search_text: str, max_urls: int = 5) -> list[str]:
    """Extract URLs from formatted search result text.

    Search results are formatted as ``URL: <url>`` lines by the search
    providers.  Returns unique URLs in order of appearance.
    """
    seen: set[str] = set()
    urls: list[str] = []
    for line in search_text.splitlines():
        if line.startswith("URL: "):
            url = line[5:].strip()
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
                if len(urls) >= max_urls:
                    break
    return urls


def extract_search_results(
    search_text: str,
) -> list[tuple[str, str, str]]:
    """Extract ``(title, url, snippet)`` tuples from formatted search output.

    Both DuckDuckGo and SearXNG providers format results as::

        Title: <title>
        URL: <url>
        <snippet body>

        ---

    Returns a list of tuples preserving order.
    """
    results: list[tuple[str, str, str]] = []
    blocks = search_text.split("---")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        title = ""
        url = ""
        snippet_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("Title: "):
                title = line[7:].strip()
            elif line.startswith("URL: "):
                url = line[5:].strip()
            else:
                snippet_lines.append(line)
        if url:
            snippet = " ".join(
                ln.strip() for ln in snippet_lines if ln.strip()
            )
            results.append((title, url, snippet[:300]))
    return results


# Domains that indicate package registries, repository hosts, or Q&A
# sites rather than documentation — deprioritized when selecting the
# best URL from each search category.
_DEPRIORITIZED_DOMAINS = (
    "packagist.org",
    "npmjs.com",
    "pypi.org",
    "hub.docker.com",
    "github.com",
    "stackoverflow.com",
    "stackexchange.com",
)


def select_one_per_query(
    query_results: list[list[tuple[str, str, str]]],
) -> list[list[str]]:
    """Pick ranked candidate URLs per search query.

    Iterates each query's result list and returns an ordered list of
    candidate URLs for each query.  The first URL is the preferred pick
    (non-deprioritized domain when possible), followed by fallbacks in
    case the primary fetch fails (403, 404, timeout, etc.).

    Candidates are globally deduped — a URL picked as primary for one
    query won't appear as a candidate for another.

    Returns a list of URL lists — one list per query that produced
    results.
    """
    selected: set[str] = set()
    picks: list[list[str]] = []

    for results in query_results:
        # Dedup within this query's results, skip globally selected
        seen: set[str] = set()
        candidates: list[str] = []
        for _title, url, _snippet in results:
            if url not in seen and url not in selected:
                seen.add(url)
                candidates.append(url)

        if not candidates:
            continue

        # Prefer URLs not from deprioritized domains
        preferred = [
            u for u in candidates
            if not any(d in u for d in _DEPRIORITIZED_DOMAINS)
        ]
        deprioritized = [
            u for u in candidates
            if any(d in u for d in _DEPRIORITIZED_DOMAINS)
        ]

        # Ordered: preferred first, then deprioritized as fallbacks
        ranked = preferred + deprioritized
        # Reserve the primary pick globally so other queries don't reuse it
        selected.add(ranked[0])
        picks.append(ranked)

    return picks
