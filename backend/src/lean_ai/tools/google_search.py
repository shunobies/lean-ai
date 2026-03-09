"""Google search via headless Chrome + BeautifulSoup parsing.

Lazily initializes a headless Chrome browser and reuses it across
sequential searches.  The browser is closed explicitly via
``close_browser()`` or automatically via ``atexit``.

Requires the ``selenium`` package — install via ``pip install lean-ai[google]``.
The user must also have Chrome (or Chromium) installed.  Selenium 4.x
bundles ``selenium-manager`` which auto-downloads the matching chromedriver.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import random
import time
from threading import Lock
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from lean_ai.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Browser singleton — lazily initialized, reused across searches
# ---------------------------------------------------------------------------

_browser = None
_browser_lock = Lock()


def _get_browser():
    """Return the shared headless Chrome instance, creating it on first call.

    Raises :class:`ImportError` if ``selenium`` is not installed.
    Raises :class:`RuntimeError` if Chrome/chromedriver cannot start.
    """
    global _browser
    if _browser is not None:
        return _browser

    with _browser_lock:
        # Double-checked locking
        if _browser is not None:
            return _browser

        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except ImportError:
            raise ImportError(
                "selenium is not installed. "
                "Install with: pip install lean-ai[google]"
            )

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument(
            "--disable-blink-features=AutomationControlled",
        )
        options.add_experimental_option(
            "excludeSwitches", ["enable-automation"],
        )
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument(
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

        try:
            service = Service()
            _browser = webdriver.Chrome(service=service, options=options)
            # Remove navigator.webdriver fingerprint
            _browser.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": (
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined})"
                    ),
                },
            )
            atexit.register(close_browser)
            logger.info("Google search: headless Chrome initialized")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to start headless Chrome: {exc}. "
                "Ensure Chrome and chromedriver are installed."
            ) from exc

        return _browser


def close_browser() -> None:
    """Explicitly close the browser.  Safe to call multiple times."""
    global _browser
    with _browser_lock:
        if _browser is not None:
            try:
                _browser.quit()
                logger.info("Google search: headless Chrome closed")
            except Exception:
                pass
            _browser = None


# ---------------------------------------------------------------------------
# Cookie consent handler
# ---------------------------------------------------------------------------

def _dismiss_consent(browser) -> None:
    """Dismiss Google cookie consent dialog if present.

    Google shows a consent page at ``consent.google.com`` in EU regions.
    Clicks "Reject all" first (more privacy-preserving), falling back
    to "Accept all".
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        if "consent.google" not in browser.current_url:
            return

        # Try "Reject all" first
        try:
            btn = WebDriverWait(browser, 3).until(
                expected_conditions.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Reject all')]"),
                ),
            )
            btn.click()
            time.sleep(0.5)
            return
        except Exception:
            pass

        # Fall back to "Accept all"
        try:
            btn = WebDriverWait(browser, 3).until(
                expected_conditions.element_to_be_clickable(
                    (By.XPATH, "//button[contains(., 'Accept all')]"),
                ),
            )
            btn.click()
            time.sleep(0.5)
        except Exception:
            logger.warning(
                "Google search: could not dismiss consent dialog",
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Google HTML result parser
# ---------------------------------------------------------------------------

def _parse_google_results(
    page_source: str,
    max_results: int = 10,
) -> list[dict[str, str]]:
    """Parse a Google search result page into structured results.

    Multi-fallback strategy to handle Google's changing HTML:

    1. ``div.g`` containers (stable selector for years)
    2. ``h3`` for title, parent ``<a>`` for URL
    3. Several known snippet selectors (VwiC3b, data-sncf, IsZvec)
    4. Broad ``h3``-scan fallback when ``div.g`` yields nothing

    Returns ``[{"title": ..., "url": ..., "snippet": ...}]``.
    """
    soup = BeautifulSoup(page_source, "html.parser")
    results: list[dict[str, str]] = []

    search_div = soup.find("div", id="search") or soup
    result_divs = search_div.find_all(
        "div", class_="g", limit=max_results + 5,
    )

    for div in result_divs:
        if len(results) >= max_results:
            break

        # Title from h3
        h3 = div.find("h3")
        if not h3:
            continue
        title = h3.get_text(strip=True)

        # URL from the <a> wrapping or near the h3
        link = h3.find_parent("a")
        if not link:
            link = div.find("a", href=True)
        if not link or not link.get("href"):
            continue
        url = link["href"]
        # Skip Google internal links
        if url.startswith("/") or "google.com" in url:
            continue

        # Snippet — try several known selectors
        snippet = ""
        snippet_el = div.find("div", attrs={"data-sncf": True})
        if not snippet_el:
            snippet_el = div.find(
                "div",
                class_=lambda c: c and "VwiC3b" in c,
            )
        if not snippet_el:
            iszvec = div.find("div", class_="IsZvec")
            if iszvec:
                snippet_el = iszvec
        if snippet_el:
            snippet = snippet_el.get_text(separator=" ", strip=True)[:300]
        else:
            # Last resort: all text minus the title
            all_text = div.get_text(separator=" ", strip=True)
            if all_text.startswith(title):
                snippet = all_text[len(title):].strip()[:300]

        results.append({"title": title, "url": url, "snippet": snippet})

    # Fallback: if div.g found nothing, scan h3 tags directly
    if not results:
        for h3 in search_div.find_all("h3", limit=max_results + 5):
            if len(results) >= max_results:
                break
            link = h3.find_parent("a")
            if not link or not link.get("href"):
                continue
            url = link["href"]
            if url.startswith("/") or "google.com" in url:
                continue
            title = h3.get_text(strip=True)
            parent = link.parent
            if parent:
                full_text = parent.get_text(separator=" ", strip=True)
                snippet = full_text.replace(title, "", 1).strip()[:300]
            else:
                snippet = ""
            results.append(
                {"title": title, "url": url, "snippet": snippet},
            )

    return results


# ---------------------------------------------------------------------------
# Async search function (matches provider signature)
# ---------------------------------------------------------------------------

_last_search_time: float = 0.0


def _do_google_search(query: str, max_results: int = 10) -> str:
    """Synchronous Google search — runs in a thread pool.

    Returns results in the standard ``Title:/URL:/snippet`` format
    used by all search providers.
    """
    global _last_search_time

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions
    from selenium.webdriver.support.ui import WebDriverWait

    # Rate limiting with random jitter to avoid CAPTCHA
    base_delay = settings.search_delay
    elapsed = time.monotonic() - _last_search_time
    target_delay = base_delay + random.uniform(0, base_delay)
    if elapsed < target_delay:
        time.sleep(target_delay - elapsed)

    browser = _get_browser()

    # Navigate directly to search results
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en"
    browser.get(url)
    _last_search_time = time.monotonic()

    # Handle consent dialog (EU regions)
    _dismiss_consent(browser)

    # Wait for results to load
    try:
        WebDriverWait(browser, 10).until(
            expected_conditions.presence_of_element_located((By.ID, "search")),
        )
    except Exception:
        # May still have results without #search div
        pass

    # Parse results
    results = _parse_google_results(browser.page_source, max_results)

    if not results:
        return f"No results found for: {query}"

    parts: list[str] = []
    for r in results:
        parts.append(
            f"Title: {r['title']}\nURL: {r['url']}\n{r['snippet']}",
        )

    return "\n\n---\n\n".join(parts)


async def search_google(query: str, max_results: int = 10) -> str:
    """Async wrapper for Google search.  Matches provider signature."""
    return await asyncio.to_thread(_do_google_search, query, max_results)
