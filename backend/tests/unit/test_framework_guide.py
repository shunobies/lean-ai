"""Tests for framework guide detection and query building.

Pure unit tests — no LLM, no network calls required.
"""

import json
from unittest.mock import AsyncMock

import pytest

from lean_ai.context.framework_guide import (
    _build_guide_search_queries,
    _canonicalize_name,
    _check_invalid_paths,
    _deduplicate_sections,
    _deduplicate_sections_mechanical,
    _extract_file_paths,
    _extract_php_class_refs,
    _extract_search_results,
    _extract_urls_from_search,
    _find_block_boundaries,
    _get_primary_frameworks,
    _remove_blocks,
    _renumber_steps,
    _repair_code_blocks,
    _select_one_per_query,
)

# ---------------------------------------------------------------------------
# _get_primary_frameworks
# ---------------------------------------------------------------------------


class TestGetPrimaryFrameworks:
    def test_laravel_detected(self, tmp_path):
        comp = tmp_path / "composer.json"
        comp.write_text(json.dumps({
            "require": {"php": "^8.4", "laravel/framework": "^12.0"},
        }))
        frameworks, runtimes = _get_primary_frameworks(str(tmp_path))
        fw_names = [n for n, _v in frameworks]
        rt_names = [n for n, _v in runtimes]
        assert "laravel/framework" in fw_names
        assert "PHP" in rt_names

    def test_django_detected(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nrequires-python = ">=3.12"\n'
            'dependencies = ["django>=5.0"]\n'
        )
        frameworks, runtimes = _get_primary_frameworks(str(tmp_path))
        fw_names = [n for n, _v in frameworks]
        assert "django" in fw_names

    def test_react_next_detected(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"react": "^18.2.0", "next": "^14.0.0"},
        }))
        frameworks, runtimes = _get_primary_frameworks(str(tmp_path))
        fw_names = [n for n, _v in frameworks]
        assert "react" in fw_names or "next" in fw_names

    def test_rails_detected(self, tmp_path):
        gemfile = tmp_path / "Gemfile"
        gemfile.write_text(
            "source 'https://rubygems.org'\n"
            "ruby '3.2.0'\n"
            "gem 'rails', '~> 7.1'\n"
        )
        frameworks, runtimes = _get_primary_frameworks(str(tmp_path))
        fw_names = [n for n, _v in frameworks]
        assert "rails" in fw_names

    def test_no_frameworks(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("requests>=2.31\nhttpx>=0.25\n")
        frameworks, runtimes = _get_primary_frameworks(str(tmp_path))
        assert frameworks == []

    def test_empty_project(self, tmp_path):
        frameworks, runtimes = _get_primary_frameworks(str(tmp_path))
        assert frameworks == []
        assert runtimes == []

    def test_caps_at_three_frameworks(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {
                "react": "^18.0.0",
                "next": "^14.0.0",
                "vue": "^3.4.0",
                "angular": "^17.0.0",
                "svelte": "^4.0.0",
            },
        }))
        frameworks, _runtimes = _get_primary_frameworks(str(tmp_path))
        assert len(frameworks) <= 3


# ---------------------------------------------------------------------------
# _build_guide_search_queries
# ---------------------------------------------------------------------------


class TestBuildGuideSearchQueries:
    def test_generates_queries_for_laravel(self):
        frameworks = [("laravel/framework", "^12.0")]
        runtimes = [("PHP", "^8.4")]
        queries = _build_guide_search_queries(frameworks, runtimes)
        assert len(queries) >= 2
        assert any("laravel" in q.lower() for q in queries)
        assert any("architecture" in q.lower() for q in queries)
        assert any("CLI" in q or "scaffolding" in q.lower() for q in queries)

    def test_generates_queries_for_django(self):
        frameworks = [("django", ">=5.0")]
        runtimes = [("Python", ">=3.12")]
        queries = _build_guide_search_queries(frameworks, runtimes)
        assert len(queries) >= 2
        assert any("django" in q.lower() for q in queries)

    def test_caps_at_sixteen(self):
        frameworks = [
            (f"framework{i}", f">={i}.0") for i in range(10)
        ]
        queries = _build_guide_search_queries(frameworks, [])
        assert len(queries) <= 16

    def test_empty_frameworks(self):
        queries = _build_guide_search_queries([], [])
        assert queries == []

    def test_cutoff_adds_changelog_query(self):
        frameworks = [("laravel/framework", "^12.0")]
        runtimes = [("PHP", "^8.4")]
        queries = _build_guide_search_queries(
            frameworks, runtimes, cutoff="2024-04",
        )
        assert any("changelog" in q.lower() for q in queries)

    def test_no_cutoff_no_changelog_query(self):
        frameworks = [("laravel/framework", "^12.0")]
        runtimes = [("PHP", "^8.4")]
        queries = _build_guide_search_queries(
            frameworks, runtimes, cutoff=None,
        )
        assert not any("changelog" in q.lower() for q in queries)

    def test_queries_are_framework_agnostic(self):
        """Queries should not contain framework-specific CLI names."""
        frameworks = [("django", ">=5.0")]
        runtimes = [("Python", ">=3.12")]
        queries = _build_guide_search_queries(frameworks, runtimes)
        joined = " ".join(queries).lower()
        assert "artisan" not in joined
        assert "make:model" not in joined

    def test_upgrade_query_generated(self):
        frameworks = [("laravel/framework", "^12.0")]
        queries = _build_guide_search_queries(frameworks, [])
        assert any("upgrade" in q.lower() for q in queries)

    def test_middleware_query_generated(self):
        frameworks = [("laravel/framework", "^12.0")]
        queries = _build_guide_search_queries(frameworks, [])
        assert any("middleware" in q.lower() for q in queries)

    def test_testing_query_generated(self):
        frameworks = [("laravel/framework", "^12.0")]
        queries = _build_guide_search_queries(frameworks, [])
        assert any("testing" in q.lower() for q in queries)

    def test_pitfalls_query_generated(self):
        frameworks = [("laravel/framework", "^12.0")]
        queries = _build_guide_search_queries(frameworks, [])
        assert any("pitfalls" in q.lower() for q in queries)


# ---------------------------------------------------------------------------
# _extract_urls_from_search
# ---------------------------------------------------------------------------


class TestExtractUrlsFromSearch:
    def test_extracts_urls(self):
        text = (
            "Title: Laravel Docs\n"
            "URL: https://laravel.com/docs/12.x\n"
            "Some snippet text\n\n"
            "---\n\n"
            "Title: Another Result\n"
            "URL: https://example.com/guide\n"
            "More text"
        )
        urls = _extract_urls_from_search(text)
        assert urls == [
            "https://laravel.com/docs/12.x",
            "https://example.com/guide",
        ]

    def test_deduplicates(self):
        text = (
            "URL: https://laravel.com/docs\nsnippet\n\n"
            "URL: https://laravel.com/docs\nsnippet again"
        )
        urls = _extract_urls_from_search(text)
        assert len(urls) == 1

    def test_respects_max(self):
        lines = [f"URL: https://example.com/{i}\n" for i in range(20)]
        text = "\n".join(lines)
        urls = _extract_urls_from_search(text, max_urls=3)
        assert len(urls) == 3

    def test_empty_input(self):
        assert _extract_urls_from_search("") == []
        assert _extract_urls_from_search("no urls here") == []


# ---------------------------------------------------------------------------
# _extract_file_paths
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    def test_extracts_backtick_paths(self):
        text = "Edit `app/Http/Kernel.php` to add middleware"
        assert "app/Http/Kernel.php" in _extract_file_paths(text)

    def test_extracts_comment_paths(self):
        text = "// app/Models/Customer.php\nclass Customer {}"
        assert "app/Models/Customer.php" in _extract_file_paths(text)

    def test_ignores_urls(self):
        text = "See https://laravel.com/docs/routing.php"
        assert len(_extract_file_paths(text)) == 0

    def test_handles_blade_php(self):
        text = "Create `resources/views/index.blade.php`"
        assert "resources/views/index.blade.php" in _extract_file_paths(text)

    def test_deduplicates(self):
        text = "`app/Models/User.php` and `app/Models/User.php` again"
        paths = _extract_file_paths(text)
        assert len([p for p in paths if p == "app/Models/User.php"]) == 1

    def test_empty_input(self):
        assert _extract_file_paths("") == set()
        assert _extract_file_paths("no paths here") == set()

    def test_multiple_extensions(self):
        text = (
            "Check `config/app.php` and `resources/js/app.js` "
            "and `routes/web.php` for changes"
        )
        paths = _extract_file_paths(text)
        assert "config/app.php" in paths
        assert "resources/js/app.js" in paths
        assert "routes/web.php" in paths

    def test_strips_surrounding_punctuation(self):
        text = '("app/Http/Controllers/UserController.php")'
        paths = _extract_file_paths(text)
        assert "app/Http/Controllers/UserController.php" in paths


# ---------------------------------------------------------------------------
# _check_invalid_paths
# ---------------------------------------------------------------------------


class TestCheckInvalidPaths:
    def test_detects_invalid(self):
        text = "Edit `app/Http/Kernel.php` and `app/Models/User.php`"
        project_paths = {"app/Models/User.php", "app/Http/Controllers/HomeController.php"}
        top_dirs = {"app"}
        invalid = _check_invalid_paths(text, project_paths, top_dirs)
        assert "app/Http/Kernel.php" in invalid
        assert "app/Models/User.php" not in invalid

    def test_ignores_non_project_paths(self):
        text = "See `vendor/laravel/framework/src/Kernel.php`"
        project_paths = {"app/Models/User.php"}
        top_dirs = {"app"}
        invalid = _check_invalid_paths(text, project_paths, top_dirs)
        assert len(invalid) == 0

    def test_all_valid_returns_empty(self):
        text = "Edit `app/Models/User.php`"
        project_paths = {"app/Models/User.php"}
        top_dirs = {"app"}
        invalid = _check_invalid_paths(text, project_paths, top_dirs)
        assert invalid == set()


# ---------------------------------------------------------------------------
# _find_block_boundaries
# ---------------------------------------------------------------------------


class TestFindBlockBoundaries:
    def test_finds_fenced_code_block(self):
        lines = [
            "Some text",
            "```php",
            "// app/Http/Kernel.php",
            "class Kernel {}",
            "```",
            "More text",
        ]
        blocks = _find_block_boundaries(lines, "app/Http/Kernel.php")
        assert len(blocks) == 1
        start, end = blocks[0]
        assert start == 1  # ```php
        assert end == 5  # after closing ```

    def test_finds_list_item(self):
        lines = [
            "## Files",
            "- `app/Http/Kernel.php` — the kernel",
            "- `app/Models/User.php` — the user model",
            "",
        ]
        blocks = _find_block_boundaries(lines, "app/Http/Kernel.php")
        assert len(blocks) == 1
        start, end = blocks[0]
        assert start == 1
        assert end == 2  # just the one list item

    def test_finds_paragraph(self):
        lines = [
            "## Architecture",
            "The Kernel processes requests via app/Http/Kernel.php",
            "which dispatches to the router.",
            "",
            "Next section.",
        ]
        blocks = _find_block_boundaries(lines, "app/Http/Kernel.php")
        assert len(blocks) == 1
        start, end = blocks[0]
        # Paragraph includes the heading? No — heading stops expansion
        assert start == 1
        assert end == 3

    def test_multiple_blocks(self):
        lines = [
            "See `app/Http/Kernel.php` for middleware.",
            "",
            "```php",
            "// app/Http/Kernel.php",
            "```",
        ]
        blocks = _find_block_boundaries(lines, "app/Http/Kernel.php")
        assert len(blocks) == 2

    def test_heading_boundary(self):
        lines = [
            "## Section A",
            "References app/Http/Kernel.php here.",
            "## Section B",
            "No reference here.",
        ]
        blocks = _find_block_boundaries(lines, "app/Http/Kernel.php")
        assert len(blocks) == 1
        _start, end = blocks[0]
        assert end == 2  # stops before ## Section B

    def test_path_on_fence_line(self):
        lines = [
            "```php // app/Http/Kernel.php",
            "class Kernel {}",
            "```",
        ]
        blocks = _find_block_boundaries(lines, "app/Http/Kernel.php")
        assert len(blocks) == 1
        assert blocks[0] == (0, 3)


# ---------------------------------------------------------------------------
# _remove_blocks
# ---------------------------------------------------------------------------


class TestRemoveBlocks:
    def test_removes_code_block(self):
        text = (
            "## Middleware\n"
            "```php\n"
            "// app/Http/Kernel.php\n"
            "class Kernel {}\n"
            "```\n"
            "## Routes"
        )
        result = _remove_blocks(text, {"app/Http/Kernel.php"})
        assert "Kernel" not in result
        assert "## Middleware" in result
        assert "## Routes" in result

    def test_removes_list_item(self):
        text = (
            "- `app/Http/Kernel.php` — the kernel\n"
            "- `app/Models/User.php` — the user model"
        )
        result = _remove_blocks(text, {"app/Http/Kernel.php"})
        assert "Kernel" not in result
        assert "User.php" in result

    def test_removes_paragraph(self):
        text = (
            "## Architecture\n"
            "The Kernel in app/Http/Kernel.php handles requests.\n"
            "\n"
            "## Routes\n"
            "Routes are defined in routes/web.php."
        )
        result = _remove_blocks(text, {"app/Http/Kernel.php"})
        assert "Kernel" not in result
        assert "## Routes" in result
        assert "routes/web.php" in result

    def test_multiple_paths_single_block(self):
        text = (
            "```php\n"
            "// app/Http/Kernel.php\n"
            "// app/Http/OtherFile.php\n"
            "```\n"
            "Remaining content"
        )
        result = _remove_blocks(
            text, {"app/Http/Kernel.php", "app/Http/OtherFile.php"},
        )
        assert "Kernel" not in result
        assert "OtherFile" not in result
        assert "Remaining content" in result

    def test_collapses_blank_lines(self):
        text_with_path = (
            "Before\n\n\n\n"
            "- `app/Http/Kernel.php` — bad\n\n\n\n\n"
            "After"
        )
        result = _remove_blocks(text_with_path, {"app/Http/Kernel.php"})
        assert "Kernel" not in result
        # No runs of more than 2 consecutive blank lines
        assert "\n\n\n\n" not in result
        assert "Before" in result
        assert "After" in result

    def test_preserves_unaffected_content(self):
        text = (
            "## Good Section\n"
            "Content about routes/web.php is fine.\n"
            "\n"
            "## Bad Section\n"
            "Content about app/Http/Kernel.php is wrong."
        )
        result = _remove_blocks(text, {"app/Http/Kernel.php"})
        assert "Good Section" in result
        assert "routes/web.php" in result

    def test_no_blocks_found_returns_unchanged(self):
        text = "No file paths here at all"
        result = _remove_blocks(text, {"app/Http/Kernel.php"})
        assert result == text


# ---------------------------------------------------------------------------
# _canonicalize_name
# ---------------------------------------------------------------------------


class TestCanonicalizeName:
    def test_known_mapping(self):
        assert _canonicalize_name("laravel/framework") == "Laravel"

    def test_known_mapping_symfony(self):
        assert _canonicalize_name("symfony/framework-bundle") == "Symfony"

    def test_composer_heuristic(self):
        assert _canonicalize_name("vendor/some-pkg") == "Some Pkg"

    def test_go_module_heuristic(self):
        assert _canonicalize_name("github.com/user/gin") == "Gin"

    def test_npm_scoped_heuristic(self):
        assert _canonicalize_name("@scope/thing") == "Thing"

    def test_plain_name_unchanged(self):
        assert _canonicalize_name("django") == "django"

    def test_dotnet_mapping(self):
        assert _canonicalize_name("microsoft.aspnetcore") == "ASP.NET Core"


# ---------------------------------------------------------------------------
# _extract_search_results
# ---------------------------------------------------------------------------


class TestExtractSearchResults:
    def test_parses_standard_format(self):
        text = (
            "Title: Laravel 12 Docs\n"
            "URL: https://laravel.com/docs/12.x\n"
            "Laravel is a PHP framework\n\n"
            "---\n\n"
            "Title: Upgrade Guide\n"
            "URL: https://laravel.com/docs/12.x/upgrade\n"
            "How to upgrade from 11 to 12"
        )
        results = _extract_search_results(text)
        assert len(results) == 2
        assert results[0][0] == "Laravel 12 Docs"
        assert results[0][1] == "https://laravel.com/docs/12.x"
        assert "PHP framework" in results[0][2]
        assert results[1][1] == "https://laravel.com/docs/12.x/upgrade"

    def test_handles_missing_title(self):
        text = (
            "URL: https://example.com/page\n"
            "Some snippet"
        )
        results = _extract_search_results(text)
        assert len(results) == 1
        assert results[0][0] == ""
        assert results[0][1] == "https://example.com/page"

    def test_skips_blocks_without_url(self):
        text = (
            "Title: No URL block\n"
            "Just some text\n\n"
            "---\n\n"
            "Title: Has URL\n"
            "URL: https://example.com\n"
            "Snippet"
        )
        results = _extract_search_results(text)
        assert len(results) == 1
        assert results[0][1] == "https://example.com"

    def test_empty_input(self):
        assert _extract_search_results("") == []

    def test_multiple_results(self):
        parts = []
        for i in range(5):
            parts.append(
                f"Title: Result {i}\nURL: https://example.com/{i}\nSnippet {i}"
            )
        text = "\n\n---\n\n".join(parts)
        results = _extract_search_results(text)
        assert len(results) == 5


# ---------------------------------------------------------------------------
# _select_one_per_query
# ---------------------------------------------------------------------------


class TestSelectOnePerQuery:
    def test_picks_one_per_query(self):
        query_results = [
            [
                ("Docs", "https://laravel.com/docs", "Official"),
                ("Blog", "https://blog.example.com", "Blog"),
            ],
            [
                ("CLI Guide", "https://laravel.com/cli", "CLI docs"),
                ("Tutorial", "https://laracasts.com/cli", "Tutorial"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        assert len(picks) == 2
        assert picks[0][0] == "https://laravel.com/docs"
        assert picks[1][0] == "https://laravel.com/cli"

    def test_skips_deprioritized_domains(self):
        query_results = [
            [
                ("Packagist", "https://packagist.org/laravel", "Package"),
                ("GitHub", "https://github.com/laravel", "Repo"),
                ("Laravel Docs", "https://laravel.com/docs", "Official"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        assert len(picks) == 1
        # Preferred (non-deprioritized) is first
        assert picks[0][0] == "https://laravel.com/docs"
        # Deprioritized are fallbacks
        assert "https://packagist.org/laravel" in picks[0]
        assert "https://github.com/laravel" in picks[0]

    def test_falls_back_to_deprioritized_if_only_option(self):
        query_results = [
            [
                ("GitHub", "https://github.com/laravel", "Repo"),
                ("Packagist", "https://packagist.org/laravel", "Package"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        assert picks[0][0] == "https://github.com/laravel"

    def test_global_dedup_across_queries(self):
        """Same URL appearing in two queries should only be picked once."""
        query_results = [
            [
                ("Docs", "https://laravel.com/docs", "Official"),
            ],
            [
                ("Docs Again", "https://laravel.com/docs", "Same URL"),
                ("Upgrade", "https://laravel.com/upgrade", "Upgrade"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        assert len(picks) == 2
        assert picks[0][0] == "https://laravel.com/docs"
        assert picks[1][0] == "https://laravel.com/upgrade"

    def test_skips_empty_query_results(self):
        query_results = [
            [],
            [("Docs", "https://laravel.com/docs", "Official")],
            [],
        ]
        picks = _select_one_per_query(query_results)
        assert len(picks) == 1
        assert picks[0][0] == "https://laravel.com/docs"

    def test_all_empty_returns_nothing(self):
        query_results = [[], [], []]
        picks = _select_one_per_query(query_results)
        assert picks == []

    def test_eight_queries_eight_picks(self):
        """Simulates realistic 8-category search with one pick each."""
        query_results = [
            [("R1", f"https://example.com/cat{i}", "Snippet")]
            for i in range(8)
        ]
        picks = _select_one_per_query(query_results)
        assert len(picks) == 8
        # Each pick list has the primary URL
        primaries = [p[0] for p in picks]
        assert len(set(primaries)) == 8  # all unique

    def test_dedup_within_single_query(self):
        query_results = [
            [
                ("Docs", "https://laravel.com/docs", "First"),
                ("Docs", "https://laravel.com/docs", "Duplicate"),
                ("Other", "https://other.com", "Other"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        assert picks[0][0] == "https://laravel.com/docs"
        # "other.com" is a fallback
        assert "https://other.com" in picks[0]

    def test_returns_fallback_urls(self):
        """Each query returns ranked candidates, not just primary."""
        query_results = [
            [
                ("Docs", "https://laravel.com/docs", "Official"),
                ("Blog", "https://blog.example.com", "Blog post"),
                ("GitHub", "https://github.com/laravel", "Repo"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        assert len(picks) == 1
        # Primary is non-deprioritized
        assert picks[0][0] == "https://laravel.com/docs"
        # Has fallbacks
        assert len(picks[0]) == 3
        # Deprioritized (github) comes last
        assert picks[0][-1] == "https://github.com/laravel"

    def test_preferred_before_deprioritized_in_ranking(self):
        """Non-deprioritized URLs come before deprioritized in ranked list."""
        query_results = [
            [
                ("GH", "https://github.com/laravel", "Repo"),
                ("Docs", "https://laravel.com/docs", "Official"),
                ("SO", "https://stackoverflow.com/q/123", "Q&A"),
                ("Blog", "https://blog.example.com", "Blog"),
            ],
        ]
        picks = _select_one_per_query(query_results)
        ranked = picks[0]
        # Preferred (laravel.com, blog) come before deprioritized (github, SO)
        assert ranked[0] == "https://laravel.com/docs"
        assert ranked[1] == "https://blog.example.com"
        assert "https://github.com/laravel" in ranked[2:]
        assert "https://stackoverflow.com/q/123" in ranked[2:]


# ---------------------------------------------------------------------------
# _deduplicate_sections_mechanical
# ---------------------------------------------------------------------------


class TestDeduplicateSectionsMechanical:
    def test_no_duplicates_unchanged(self):
        text = (
            "## Framework Architecture\n"
            "Content A\n\n"
            "## Common CLI Commands\n"
            "Content B\n\n"
            "## File Organization Conventions\n"
            "Content C"
        )
        result = _deduplicate_sections_mechanical(text)
        assert result == text

    def test_removes_second_occurrence(self):
        text = (
            "## Framework Architecture\n"
            "First version\n\n"
            "## Framework Architecture\n"
            "Second version\n"
        )
        result = _deduplicate_sections_mechanical(text)
        assert result.count("## Framework Architecture") == 1
        assert "First version" in result
        assert "Second version" not in result

    def test_preserves_preamble(self):
        text = (
            "# Framework Guide\n\n"
            "Some intro text.\n\n"
            "## Section One\n"
            "Content\n\n"
            "## Section One\n"
            "Duplicate"
        )
        result = _deduplicate_sections_mechanical(text)
        assert "# Framework Guide" in result
        assert "Some intro text." in result
        assert result.count("## Section One") == 1

    def test_multiple_duplicates(self):
        text = (
            "## Architecture\n"
            "First\n\n"
            "## Architecture\n"
            "Second\n\n"
            "## Architecture\n"
            "Third"
        )
        result = _deduplicate_sections_mechanical(text)
        assert result.count("## Architecture") == 1
        assert "First" in result
        assert "Second" not in result
        assert "Third" not in result

    def test_collapses_blank_lines(self):
        text = (
            "## Section A\n"
            "Content A\n\n\n\n"
            "## Section A\n"
            "Duplicate\n\n\n\n"
            "## Section B\n"
            "Content B"
        )
        result = _deduplicate_sections_mechanical(text)
        # Max 2 blank lines (3 newlines) — no runs of 4+ newlines
        assert "\n\n\n\n" not in result
        assert "## Section B" in result

    def test_no_headings_unchanged(self):
        text = "Just plain text with no headings."
        result = _deduplicate_sections_mechanical(text)
        assert result == text


# ---------------------------------------------------------------------------
# _deduplicate_sections (LLM-based)
# ---------------------------------------------------------------------------


class TestDeduplicateSections:
    @pytest.mark.asyncio
    async def test_llm_identifies_duplicates(self):
        text = (
            "## Framework Architecture\n"
            "Laravel follows MVC with controllers and models.\n\n"
            "## Component Relationships\n"
            "Routes map to controllers via web.php.\n\n"
            "## Framework Architecture\n"
            "The architecture is based on MVC pattern."
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="3")

        result = await _deduplicate_sections(text, mock_llm)
        assert result.count("## Framework Architecture") == 1
        assert "Laravel follows MVC" in result
        assert "## Component Relationships" in result
        assert "The architecture is based on" not in result

    @pytest.mark.asyncio
    async def test_llm_returns_none(self):
        text = (
            "## Framework Architecture\n"
            "Content A\n\n"
            "## Component Relationships\n"
            "Content B"
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(return_value="NONE")

        result = await _deduplicate_sections(text, mock_llm)
        assert result == text

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_mechanical(self):
        text = (
            "## Architecture\n"
            "First version\n\n"
            "## Architecture\n"
            "Second version"
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(side_effect=Exception("LLM down"))

        result = await _deduplicate_sections(text, mock_llm)
        # Mechanical fallback catches exact heading match
        assert result.count("## Architecture") == 1
        assert "First version" in result
        assert "Second version" not in result

    @pytest.mark.asyncio
    async def test_llm_unparseable_falls_back(self):
        text = (
            "## Section A\n"
            "Content A\n\n"
            "## Section A\n"
            "Content A again"
        )
        mock_llm = AsyncMock()
        mock_llm.chat_raw = AsyncMock(
            return_value="I think sections look fine",
        )

        result = await _deduplicate_sections(text, mock_llm)
        # Unparseable → mechanical fallback catches exact match
        assert result.count("## Section A") == 1

    @pytest.mark.asyncio
    async def test_fewer_than_two_sections_skips_llm(self):
        text = "## Only One Section\nSome content here."
        mock_llm = AsyncMock()

        result = await _deduplicate_sections(text, mock_llm)
        assert result == text
        mock_llm.chat_raw.assert_not_called()


# ---------------------------------------------------------------------------
# _extract_php_class_refs
# ---------------------------------------------------------------------------


class TestExtractPhpClassRefs:
    def test_basic_namespace(self):
        text = "The App\\Http\\Kernel handles requests"
        refs = _extract_php_class_refs(text)
        assert "app/Http/Kernel.php" in refs
        assert refs["app/Http/Kernel.php"] == "App\\Http\\Kernel"

    def test_backtick_wrapped(self):
        text = "Edit `App\\Models\\User` to add the trait"
        refs = _extract_php_class_refs(text)
        assert "app/Models/User.php" in refs
        assert refs["app/Models/User.php"] == "App\\Models\\User"

    def test_skips_stdlib(self):
        text = "Uses Illuminate\\Foundation\\Application internally"
        refs = _extract_php_class_refs(text)
        assert len(refs) == 0

    def test_multiple_refs(self):
        text = "App\\Http\\Kernel and App\\Models\\User are key classes"
        refs = _extract_php_class_refs(text)
        assert len(refs) == 2
        assert "app/Http/Kernel.php" in refs
        assert "app/Models/User.php" in refs

    def test_single_segment_skipped(self):
        text = "The App class does something"
        refs = _extract_php_class_refs(text)
        assert len(refs) == 0

    def test_empty_input(self):
        assert _extract_php_class_refs("") == {}


# ---------------------------------------------------------------------------
# Extended _check_invalid_paths — PHP namespace tests
# ---------------------------------------------------------------------------


class TestCheckInvalidPathsPhp:
    def test_detects_invalid_php_namespace(self):
        text = "The App\\Http\\Kernel processes requests"
        project_paths = {"app/Models/User.php"}
        top_dirs = {"app"}
        invalid = _check_invalid_paths(text, project_paths, top_dirs)
        assert "App\\Http\\Kernel" in invalid

    def test_valid_php_namespace_not_flagged(self):
        text = "See App\\Models\\User for the model"
        project_paths = {"app/Models/User.php"}
        top_dirs = {"app"}
        invalid = _check_invalid_paths(text, project_paths, top_dirs)
        assert len(invalid) == 0


# ---------------------------------------------------------------------------
# _get_primary_frameworks — vite not detected as framework
# ---------------------------------------------------------------------------


class TestViteNotFramework:
    def test_vite_not_detected_as_framework(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "devDependencies": {"vite": "^7.0.0"},
        }))
        frameworks, _runtimes = _get_primary_frameworks(str(tmp_path))
        fw_names = [n for n, _v in frameworks]
        assert "vite" not in fw_names


# ---------------------------------------------------------------------------
# _repair_code_blocks
# ---------------------------------------------------------------------------


class TestRepairCodeBlocks:
    def test_wraps_unfenced_php(self):
        text = (
            "Here is the route:\n"
            "Route::get('/users', [UserController::class, 'index']);\n"
            "\n"
            "And the controller:"
        )
        result = _repair_code_blocks(text)
        assert "```php" in result
        assert "Route::get" in result
        assert result.count("```") == 2  # open + close

    def test_wraps_unfenced_bash(self):
        text = (
            "Run this command:\n"
            "$ php artisan make:model Customer\n"
            "\n"
            "This creates the model."
        )
        result = _repair_code_blocks(text)
        assert "```bash" in result
        assert "php artisan" in result

    def test_preserves_already_fenced(self):
        text = (
            "```php\n"
            "Route::get('/', fn() => view('welcome'));\n"
            "```"
        )
        result = _repair_code_blocks(text)
        assert result == text

    def test_splits_mixed_languages(self):
        text = (
            "Run the command:\n"
            "$ php artisan make:controller UserController\n"
            "Then edit the controller:\n"
            "class UserController extends Controller {}\n"
        )
        result = _repair_code_blocks(text)
        assert "```bash" in result
        assert "```php" in result

    def test_no_code_lines_unchanged(self):
        text = "This is just prose about the framework."
        result = _repair_code_blocks(text)
        assert result == text

    def test_wraps_dollar_variable(self):
        text = (
            "Access the user:\n"
            "$user = User::find(1);\n"
            "$user->name = 'Alex';\n"
        )
        result = _repair_code_blocks(text)
        assert "```php" in result
        assert result.count("```") == 2


# ---------------------------------------------------------------------------
# _renumber_steps
# ---------------------------------------------------------------------------


class TestRenumberSteps:
    def test_renumbers_after_gap(self):
        text = (
            "## Adding a New Feature\n"
            "1. Create the model\n"
            "3. Add routes\n"
            "5. Create the view\n"
        )
        result = _renumber_steps(text)
        assert "1. Create the model" in result
        assert "2. Add routes" in result
        assert "3. Create the view" in result

    def test_renumbers_step_headings(self):
        text = (
            "## Adding a New Feature\n"
            "### Step 1: Create model\n"
            "Content here\n"
            "### Step 3: Add routes\n"
            "More content\n"
        )
        result = _renumber_steps(text)
        assert "### Step 1:" in result
        assert "### Step 2:" in result
        assert "### Step 3:" not in result

    def test_resets_at_section_boundary(self):
        text = (
            "## Section A\n"
            "1. First\n"
            "3. Third\n"
            "\n"
            "## Section B\n"
            "2. Second item\n"
            "4. Fourth item\n"
        )
        result = _renumber_steps(text)
        # Section A: renumbered to 1, 2
        assert "1. First" in result
        assert "2. Third" in result
        # Section B: renumbered to 1, 2 (fresh start)
        assert "1. Second item" in result
        assert "2. Fourth item" in result

    def test_no_numbers_unchanged(self):
        text = "## Section\nJust prose here.\nMore prose."
        result = _renumber_steps(text)
        assert result == text
