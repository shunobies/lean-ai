"""Tests for deprecation version detection and query generation.

Pure unit tests — no LLM, no network calls required.
"""

import json

from lean_ai.context.deprecations import (
    DetectedDependency,
    _build_search_queries,
    _detect_versions,
    _extract_major_minor,
    _parse_pep508,
    _parse_requirement_line,
)

# ---------------------------------------------------------------------------
# _extract_major_minor
# ---------------------------------------------------------------------------


class TestExtractMajorMinor:
    def test_simple_version(self):
        assert _extract_major_minor("3.12") == "3.12"

    def test_three_segments(self):
        assert _extract_major_minor("4.2.1") == "4.2"

    def test_gte_prefix(self):
        assert _extract_major_minor(">=3.12") == "3.12"

    def test_caret_prefix(self):
        assert _extract_major_minor("^18.2.0") == "18.2"

    def test_tilde_arrow(self):
        assert _extract_major_minor("~> 7.1") == "7.1"

    def test_range_stops_at_comma(self):
        assert _extract_major_minor(">=3.12,<4") == "3.12"

    def test_double_equals(self):
        assert _extract_major_minor("==4.2.1") == "4.2"

    def test_single_segment(self):
        assert _extract_major_minor("17") == "17"

    def test_empty_string(self):
        assert _extract_major_minor("") == ""

    def test_no_digits(self):
        assert _extract_major_minor("latest") == ""


# ---------------------------------------------------------------------------
# _parse_pep508
# ---------------------------------------------------------------------------


class TestParsePep508:
    def test_pinned_range(self):
        name, ver = _parse_pep508("django>=4.2,<5.0")
        assert name == "django"
        assert ver == ">=4.2,<5.0"

    def test_bare_name(self):
        name, ver = _parse_pep508("requests")
        assert name == "requests"
        assert ver == ""

    def test_gte(self):
        name, ver = _parse_pep508("uvicorn>=0.20")
        assert name == "uvicorn"
        assert ver == ">=0.20"

    def test_with_extras(self):
        name, ver = _parse_pep508("uvicorn[standard]>=0.20")
        assert name == "uvicorn"
        assert "0.20" in ver


# ---------------------------------------------------------------------------
# _parse_requirement_line
# ---------------------------------------------------------------------------


class TestParseRequirementLine:
    def test_pinned(self):
        name, ver = _parse_requirement_line("django==4.2.1")
        assert name == "django"
        assert ver == "==4.2.1"

    def test_gte(self):
        name, ver = _parse_requirement_line("celery>=5.3")
        assert name == "celery"
        assert ver == ">=5.3"

    def test_bare(self):
        name, ver = _parse_requirement_line("requests")
        assert name == "requests"
        assert ver == ""


# ---------------------------------------------------------------------------
# _detect_versions — per-ecosystem via tmp_path fixtures
# ---------------------------------------------------------------------------


class TestDetectVersions:
    def test_pyproject_toml(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nrequires-python = ">=3.12"\n'
            'dependencies = ["django>=4.2", "celery>=5.3"]\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Python" in names
        assert "django" in names
        assert "celery" in names

    def test_requirements_txt_fallback(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask==3.0.0\nrequests>=2.31\n")
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "flask" in names
        assert "requests" in names

    def test_package_json(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(json.dumps({
            "dependencies": {"react": "^18.2.0", "next": "^14.0.0"},
            "engines": {"node": ">=18"},
        }))
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Node.js" in names
        assert "react" in names
        assert "next" in names

    def test_composer_json(self, tmp_path):
        comp = tmp_path / "composer.json"
        comp.write_text(json.dumps({
            "require": {"php": "^8.4", "laravel/framework": "^12.0"},
        }))
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "PHP" in names
        assert "laravel/framework" in names

    def test_go_mod(self, tmp_path):
        gomod = tmp_path / "go.mod"
        gomod.write_text(
            "module example.com/myapp\n\ngo 1.22\n\n"
            "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n)\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Go" in names
        assert "github.com/gin-gonic/gin" in names

    def test_gemfile(self, tmp_path):
        gemfile = tmp_path / "Gemfile"
        gemfile.write_text(
            "source 'https://rubygems.org'\n"
            "ruby '3.2.0'\n"
            "gem 'rails', '~> 7.1'\n"
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Ruby" in names
        assert "rails" in names

    def test_cargo_toml(self, tmp_path):
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text(
            '[package]\nname = "myapp"\nedition = "2021"\n\n'
            '[dependencies]\naxum = "0.7"\n'
        )
        deps = _detect_versions(str(tmp_path))
        names = [d.name for d in deps]
        assert "Rust" in names
        assert "axum" in names

    def test_no_files(self, tmp_path):
        deps = _detect_versions(str(tmp_path))
        assert deps == []


# ---------------------------------------------------------------------------
# _build_search_queries
# ---------------------------------------------------------------------------


class TestBuildSearchQueries:
    def test_prioritizes_runtimes_and_frameworks(self):
        deps = [
            DetectedDependency("Python", ">=3.12", "runtime"),
            DetectedDependency("django", ">=4.2", "framework"),
            DetectedDependency("requests", ">=2.31", "library"),
        ]
        queries = _build_search_queries(deps)
        assert len(queries) >= 2
        assert any("Python 3.12" in q for q in queries)
        assert any("django 4.2" in q for q in queries)

    def test_libraries_fill_remaining_budget(self):
        deps = [
            DetectedDependency("Python", ">=3.12", "runtime"),
            DetectedDependency("requests", ">=2.31", "library"),
        ]
        queries = _build_search_queries(deps)
        assert any("requests" in q for q in queries)

    def test_respects_max_cap(self):
        deps = [
            DetectedDependency(f"framework{i}", f">={i}.0", "framework")
            for i in range(20)
        ]
        queries = _build_search_queries(deps)
        assert len(queries) <= 5  # default cap

    def test_skips_empty_versions(self):
        deps = [
            DetectedDependency("mystery", "", "framework"),
        ]
        queries = _build_search_queries(deps)
        assert len(queries) == 0

    def test_empty_deps(self):
        queries = _build_search_queries([])
        assert queries == []
