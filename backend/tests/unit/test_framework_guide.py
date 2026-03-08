"""Tests for framework guide detection and query building.

Pure unit tests — no LLM, no network calls required.
"""

import json

from lean_ai.context.framework_guide import (
    _build_guide_search_queries,
    _check_invalid_paths,
    _extract_file_paths,
    _extract_urls_from_search,
    _find_block_boundaries,
    _get_primary_frameworks,
    _remove_blocks,
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
