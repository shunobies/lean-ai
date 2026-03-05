"""Tests for language registry enhancements."""

import pytest

from lean_ai.languages.registry import get_registry


@pytest.fixture
def registry():
    return get_registry()


def test_all_package_markers(registry):
    markers = registry.all_package_markers()
    assert isinstance(markers, set)
    assert "__init__.py" in markers


def test_php_key_files_include_laravel(registry):
    lang = registry.get_language(".php")
    assert lang is not None
    assert "composer.json" in lang.key_files
    assert "routes/web.php" in lang.key_files
    assert "routes/api.php" in lang.key_files
    assert "config/app.php" in lang.key_files


def test_ruby_key_files_include_rails(registry):
    lang = registry.get_language(".rb")
    assert lang is not None
    assert "Gemfile" in lang.key_files
    assert "config/routes.rb" in lang.key_files
    assert "config/database.yml" in lang.key_files


def test_java_key_files_include_spring(registry):
    lang = registry.get_language(".java")
    assert lang is not None
    assert "pom.xml" in lang.key_files
    assert "src/main/resources/application.properties" in lang.key_files


def test_typescript_key_files_include_frameworks(registry):
    lang = registry.get_language(".ts")
    assert lang is not None
    assert "tsconfig.json" in lang.key_files
    assert "next.config.ts" in lang.key_files
    assert "vite.config.ts" in lang.key_files


def test_php_has_import_query(registry):
    lang = registry.get_language(".php")
    assert lang is not None
    assert lang.ts_import_query.strip(), "PHP import query should not be empty"


def test_ruby_has_import_query(registry):
    lang = registry.get_language(".rb")
    assert lang is not None
    assert lang.ts_import_query.strip(), "Ruby import query should not be empty"


def test_php_fan_in_strategy(registry):
    lang = registry.get_language(".php")
    assert lang is not None
    assert lang.fan_in.strategy == "backslash_to_slash"


def test_php_stdlib_prefixes(registry):
    lang = registry.get_language(".php")
    assert lang is not None
    assert "Illuminate" in lang.stdlib_prefixes
    assert "Symfony" in lang.stdlib_prefixes
