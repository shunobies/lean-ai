"""Tests for Google search HTML parsing.

Pure unit tests — no browser, no network.  Tests ``_parse_google_results``
with representative HTML fragments.
"""

from lean_ai.tools.google_search import _parse_google_results

# ---------------------------------------------------------------------------
# _parse_google_results
# ---------------------------------------------------------------------------


class TestParseGoogleResults:
    def test_standard_result(self):
        html = """
        <div id="search">
          <div class="g">
            <a href="https://laravel.com/docs/12.x">
              <h3>Laravel 12.x Documentation</h3>
            </a>
            <div class="VwiC3b">
              Laravel is a PHP web framework with expressive syntax.
            </div>
          </div>
        </div>
        """
        results = _parse_google_results(html)
        assert len(results) == 1
        assert results[0]["title"] == "Laravel 12.x Documentation"
        assert results[0]["url"] == "https://laravel.com/docs/12.x"
        assert "PHP web framework" in results[0]["snippet"]

    def test_multiple_results(self):
        html = """
        <div id="search">
          <div class="g">
            <a href="https://example.com/1"><h3>Result 1</h3></a>
            <div class="VwiC3b">Snippet 1</div>
          </div>
          <div class="g">
            <a href="https://example.com/2"><h3>Result 2</h3></a>
            <div class="VwiC3b">Snippet 2</div>
          </div>
        </div>
        """
        results = _parse_google_results(html, max_results=5)
        assert len(results) == 2
        assert results[0]["title"] == "Result 1"
        assert results[1]["title"] == "Result 2"

    def test_max_results_capped(self):
        divs = ""
        for i in range(10):
            divs += f"""
            <div class="g">
              <a href="https://example.com/{i}"><h3>Result {i}</h3></a>
              <div class="VwiC3b">Snippet {i}</div>
            </div>
            """
        html = f'<div id="search">{divs}</div>'
        results = _parse_google_results(html, max_results=3)
        assert len(results) == 3

    def test_skips_google_internal_links(self):
        html = """
        <div id="search">
          <div class="g">
            <a href="/url?q=https://example.com"><h3>Internal</h3></a>
            <div class="VwiC3b">Should skip</div>
          </div>
          <div class="g">
            <a href="https://example.com/real"><h3>Real</h3></a>
            <div class="VwiC3b">Should keep</div>
          </div>
        </div>
        """
        results = _parse_google_results(html)
        assert len(results) == 1
        assert results[0]["url"] == "https://example.com/real"

    def test_skips_google_domain_links(self):
        html = """
        <div id="search">
          <div class="g">
            <a href="https://support.google.com/help">
              <h3>Google Help</h3>
            </a>
            <div class="VwiC3b">Google help page</div>
          </div>
          <div class="g">
            <a href="https://example.com/page"><h3>Real</h3></a>
            <div class="VwiC3b">Real result</div>
          </div>
        </div>
        """
        results = _parse_google_results(html)
        assert len(results) == 1
        assert results[0]["title"] == "Real"

    def test_no_results(self):
        html = '<div id="search"><div>No results</div></div>'
        results = _parse_google_results(html)
        assert results == []

    def test_snippet_truncated_at_300(self):
        long_snippet = "x" * 500
        html = f"""
        <div id="search">
          <div class="g">
            <a href="https://example.com"><h3>Title</h3></a>
            <div class="VwiC3b">{long_snippet}</div>
          </div>
        </div>
        """
        results = _parse_google_results(html)
        assert len(results[0]["snippet"]) <= 300

    def test_fallback_h3_scan(self):
        """When div.g is absent, fall back to scanning h3 tags."""
        html = """
        <div id="search">
          <div>
            <a href="https://example.com/fallback">
              <h3>Fallback Title</h3>
            </a>
            <span>Fallback snippet text</span>
          </div>
        </div>
        """
        results = _parse_google_results(html)
        assert len(results) == 1
        assert results[0]["title"] == "Fallback Title"
        assert results[0]["url"] == "https://example.com/fallback"

    def test_data_sncf_snippet(self):
        html = """
        <div id="search">
          <div class="g">
            <a href="https://example.com"><h3>Title</h3></a>
            <div data-sncf="1">Snippet via data-sncf attr</div>
          </div>
        </div>
        """
        results = _parse_google_results(html)
        assert "data-sncf" in results[0]["snippet"]

    def test_empty_html(self):
        assert _parse_google_results("") == []
        assert _parse_google_results("<html></html>") == []


# ---------------------------------------------------------------------------
# Output format compatibility
# ---------------------------------------------------------------------------


class TestOutputFormat:
    def test_output_matches_extract_format(self):
        """Formatted output must be parseable by _extract_search_results."""
        from lean_ai.context.framework_guide import _extract_search_results

        formatted = (
            "Title: Laravel Docs\n"
            "URL: https://laravel.com/docs\n"
            "Laravel is a PHP framework\n\n"
            "---\n\n"
            "Title: Django Docs\n"
            "URL: https://docs.djangoproject.com\n"
            "Django is a Python framework"
        )
        results = _extract_search_results(formatted)
        assert len(results) == 2
        assert results[0][1] == "https://laravel.com/docs"
        assert results[1][1] == "https://docs.djangoproject.com"
