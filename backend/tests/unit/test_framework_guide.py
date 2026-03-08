"""Tests for framework guide detection and query building.

Pure unit tests — no LLM, no network calls required.
"""

import json

from lean_ai.context.framework_guide import (
    _build_guide_search_queries,
    _get_primary_frameworks,
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
        assert any("CLI" in q or "artisan" in q.lower() for q in queries)

    def test_generates_queries_for_django(self):
        frameworks = [("django", ">=5.0")]
        runtimes = [("Python", ">=3.12")]
        queries = _build_guide_search_queries(frameworks, runtimes)
        assert len(queries) >= 2
        assert any("django" in q.lower() for q in queries)

    def test_caps_at_six(self):
        frameworks = [
            (f"framework{i}", f">={i}.0") for i in range(10)
        ]
        queries = _build_guide_search_queries(frameworks, [])
        assert len(queries) <= 6

    def test_empty_frameworks(self):
        queries = _build_guide_search_queries([], [])
        assert queries == []
