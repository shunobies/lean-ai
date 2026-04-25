"""Tests for MediaWiki search and page fetching tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.fixture(autouse=True)
def _reset_wiki_state():
    """Reset module-level wiki client state between tests."""
    import lean_ai.tools.wiki as mod

    mod._client = None
    mod._authenticated = False
    yield
    mod._client = None
    mod._authenticated = False


@pytest.fixture()
def _wiki_configured():
    """Patch settings to have a wiki_url configured."""
    with patch("lean_ai.tools.wiki.settings") as mock_settings:
        mock_settings.wiki_url = "https://wiki.example.com"
        mock_settings.wiki_api_path = "/w/api.php"
        mock_settings.wiki_username = ""
        mock_settings.wiki_password = ""
        yield mock_settings


@pytest.fixture()
def _wiki_with_auth():
    """Patch settings with authentication credentials."""
    with patch("lean_ai.tools.wiki.settings") as mock_settings:
        mock_settings.wiki_url = "https://wiki.example.com"
        mock_settings.wiki_api_path = "/w/api.php"
        mock_settings.wiki_username = "bot"
        mock_settings.wiki_password = "secret"
        yield mock_settings


class TestSearchWiki:
    @pytest.mark.asyncio
    async def test_returns_error_when_not_configured(self):
        """search_wiki should fail gracefully when wiki_url is empty."""
        with patch("lean_ai.tools.wiki.settings") as mock_settings:
            mock_settings.wiki_url = ""
            from lean_ai.tools.wiki import search_wiki

            result = await search_wiki(query="test")
            assert not result.success
            assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_returns_error_on_empty_query(self, _wiki_configured):
        from lean_ai.tools.wiki import search_wiki

        result = await search_wiki(query="  ")
        assert not result.success
        assert "empty" in result.error

    @pytest.mark.asyncio
    async def test_search_returns_formatted_results(self, _wiki_configured):
        from lean_ai.tools.wiki import search_wiki

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "query": {
                "search": [
                    {
                        "title": "API Guide",
                        "snippet": "<span class='searchmatch'>API</span> documentation",
                        "wordcount": 500,
                    },
                    {
                        "title": "Architecture",
                        "snippet": "System <span class='searchmatch'>architecture</span>",
                        "wordcount": 1200,
                    },
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await search_wiki(query="API")

        assert result.success
        assert "API Guide" in result.output
        assert "Architecture" in result.output
        assert "API" in result.output
        assert "documentation" in result.output
        assert "wiki.example.com/wiki/API_Guide" in result.output

    @pytest.mark.asyncio
    async def test_search_no_results(self, _wiki_configured):
        from lean_ai.tools.wiki import search_wiki

        mock_response = MagicMock()
        mock_response.json.return_value = {"query": {"search": []}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await search_wiki(query="nonexistent")

        assert result.success
        assert "No wiki pages found" in result.output

    @pytest.mark.asyncio
    async def test_search_handles_timeout(self, _wiki_configured):
        from lean_ai.tools.wiki import search_wiki

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await search_wiki(query="test")

        assert not result.success
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_search_handles_http_error(self, _wiki_configured):
        from lean_ai.tools.wiki import search_wiki

        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=mock_response
            )
        )

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await search_wiki(query="test")

        assert not result.success
        assert "HTTP error" in result.error


class TestFetchWikiPage:
    @pytest.mark.asyncio
    async def test_returns_error_when_not_configured(self):
        with patch("lean_ai.tools.wiki.settings") as mock_settings:
            mock_settings.wiki_url = ""
            from lean_ai.tools.wiki import fetch_wiki_page

            result = await fetch_wiki_page(title="Test", repo_root="/tmp")
            assert not result.success
            assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_returns_error_on_empty_title(self, _wiki_configured):
        from lean_ai.tools.wiki import fetch_wiki_page

        result = await fetch_wiki_page(title="  ", repo_root="/tmp")
        assert not result.success
        assert "empty" in result.error

    @pytest.mark.asyncio
    async def test_fetches_and_strips_html(self, _wiki_configured):
        from lean_ai.tools.wiki import fetch_wiki_page

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "parse": {"text": {"*": "<p>Hello <b>world</b></p><script>alert('x')</script>"}}
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await fetch_wiki_page(title="Test Page", repo_root="/tmp")

        assert result.success
        assert "Hello" in result.output
        assert "world" in result.output
        assert "<script>" not in result.output
        assert "# Test Page" in result.output

    @pytest.mark.asyncio
    async def test_handles_page_not_found(self, _wiki_configured):
        from lean_ai.tools.wiki import fetch_wiki_page

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await fetch_wiki_page(title="Nonexistent", repo_root="/tmp")

        assert not result.success
        assert "doesn't exist" in result.error

    @pytest.mark.asyncio
    async def test_handles_timeout(self, _wiki_configured):
        from lean_ai.tools.wiki import fetch_wiki_page

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            result = await fetch_wiki_page(title="Test", repo_root="/tmp")

        assert not result.success
        assert "timed out" in result.error


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_skips_auth_when_no_credentials(self, _wiki_configured):
        """Should mark as authenticated without making any login calls."""
        import lean_ai.tools.wiki as mod
        from lean_ai.tools.wiki import _ensure_authenticated

        await _ensure_authenticated()
        assert mod._authenticated is True

    @pytest.mark.asyncio
    async def test_performs_login_with_credentials(self, _wiki_with_auth):
        import lean_ai.tools.wiki as mod
        from lean_ai.tools.wiki import _ensure_authenticated

        # Token response
        token_response = MagicMock()
        token_response.json.return_value = {"query": {"tokens": {"logintoken": "abc123+\\"}}}
        token_response.raise_for_status = MagicMock()

        # Login response
        login_response = MagicMock()
        login_response.json.return_value = {"login": {"result": "Success", "lgusername": "bot"}}
        login_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=token_response)
        mock_client.post = AsyncMock(return_value=login_response)

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            await _ensure_authenticated()

        assert mod._authenticated is True
        mock_client.get.assert_called_once()
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_login_failure(self, _wiki_with_auth):
        """Should mark as authenticated even on failure (to avoid retrying)."""
        import lean_ai.tools.wiki as mod
        from lean_ai.tools.wiki import _ensure_authenticated

        token_response = MagicMock()
        token_response.json.return_value = {"query": {"tokens": {"logintoken": "abc123+\\"}}}
        token_response.raise_for_status = MagicMock()

        login_response = MagicMock()
        login_response.json.return_value = {"login": {"result": "Failed", "reason": "Bad password"}}
        login_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=token_response)
        mock_client.post = AsyncMock(return_value=login_response)

        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            await _ensure_authenticated()

        # Should still be marked as authenticated to prevent retry loops
        assert mod._authenticated is True

    @pytest.mark.asyncio
    async def test_does_not_re_authenticate(self, _wiki_with_auth):
        """Second call should not make any HTTP requests."""
        import lean_ai.tools.wiki as mod
        from lean_ai.tools.wiki import _ensure_authenticated

        mod._authenticated = True

        mock_client = AsyncMock()
        with patch("lean_ai.tools.wiki._get_client", return_value=mock_client):
            await _ensure_authenticated()

        mock_client.get.assert_not_called()


class TestHtmlStripping:
    def test_strips_html_tags(self):
        from lean_ai.tools.html_utils import strip_html

        result = strip_html("<p>Hello <b>world</b></p>")
        assert "Hello" in result
        assert "world" in result
        assert "<p>" not in result
        assert "<b>" not in result

    def test_removes_scripts(self):
        from lean_ai.tools.html_utils import strip_html

        result = strip_html("<p>text</p><script>evil()</script>")
        assert "text" in result
        assert "evil" not in result

    def test_removes_style_tags(self):
        from lean_ai.tools.html_utils import strip_html

        result = strip_html("<style>.x{color:red}</style><p>content</p>")
        assert "content" in result
        assert "color" not in result


class TestPagination:
    def test_short_content_returned_as_is(self, tmp_path):
        from lean_ai.tools.html_utils import save_and_paginate

        text = "Short content\nOnly two lines"
        result = save_and_paginate(text, "wiki:Test", str(tmp_path))
        assert result == text

    def test_long_content_paginated(self, tmp_path):
        from lean_ai.tools.html_utils import save_and_paginate

        lines = [f"Line {i}" for i in range(600)]
        text = "\n".join(lines)
        result = save_and_paginate(text, "wiki:LongPage", str(tmp_path), max_lines=100)

        assert "Line 0" in result
        assert "Line 99" in result
        assert "content saved to" in result
        assert "read_file" in result

        # Verify file was written
        fetched_dir = tmp_path / ".lean_ai" / "fetched"
        assert fetched_dir.exists()
        saved_files = list(fetched_dir.iterdir())
        assert len(saved_files) == 1
        assert saved_files[0].read_text() == text


class TestToolDefinitions:
    def test_wiki_tools_included_when_configured(self):
        with patch("lean_ai.config.settings") as mock_settings:
            mock_settings.wiki_url = "https://wiki.example.com"
            from lean_ai.llm.tool_definitions import build_implementation_tools

            tools = build_implementation_tools()
            tool_names = [t["function"]["name"] for t in tools]
            assert "search_wiki" in tool_names
            assert "fetch_wiki_page" in tool_names

    def test_wiki_tools_excluded_when_not_configured(self):
        with patch("lean_ai.config.settings") as mock_settings:
            mock_settings.wiki_url = ""
            from lean_ai.llm.tool_definitions import build_implementation_tools

            tools = build_implementation_tools()
            tool_names = [t["function"]["name"] for t in tools]
            assert "search_wiki" not in tool_names
            assert "fetch_wiki_page" not in tool_names

    def test_investigation_tools_include_wiki_when_configured(self):
        with patch("lean_ai.config.settings") as mock_settings:
            mock_settings.wiki_url = "https://wiki.example.com"
            from lean_ai.llm.tool_definitions import build_investigation_tools

            tools = build_investigation_tools()
            tool_names = [t["function"]["name"] for t in tools]
            assert "search_wiki" in tool_names
            assert "fetch_wiki_page" in tool_names

    def test_planning_tools_include_wiki_when_configured(self):
        with patch("lean_ai.config.settings") as mock_settings:
            mock_settings.wiki_url = "https://wiki.example.com"
            from lean_ai.llm.tool_definitions import build_planning_tools

            tools = build_planning_tools()
            tool_names = [t["function"]["name"] for t in tools]
            assert "search_wiki" in tool_names
            assert "fetch_wiki_page" in tool_names
