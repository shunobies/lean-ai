"""Tests for tree-sitter based metadata extraction across languages."""

import pytest

from lean_ai.languages.extractor import extract_file_metadata, get_definition_nodes
from lean_ai.languages.registry import get_registry


@pytest.fixture
def registry():
    return get_registry()


@pytest.fixture
def php_lang(registry):
    lang = registry.get_language(".php")
    assert lang is not None, "PHP language definition not found"
    return lang


@pytest.fixture
def ruby_lang(registry):
    lang = registry.get_language(".rb")
    assert lang is not None, "Ruby language definition not found"
    return lang


# ---------------------------------------------------------------------------
# PHP import extraction
# ---------------------------------------------------------------------------

_PHP_USE_CODE = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\User;
use App\\Services\\AuthService;
use Illuminate\\Http\\Request;
use Illuminate\\Support\\Facades\\Cache;
"""


def test_php_use_imports_extracted(php_lang):
    meta = extract_file_metadata(_PHP_USE_CODE, php_lang)
    assert len(meta.imports) > 0, "No imports extracted from PHP use statements"
    assert len(meta.imported_modules) > 0


def test_php_use_imports_capture_full_namespace(php_lang):
    meta = extract_file_metadata(_PHP_USE_CODE, php_lang)
    modules = set(meta.imported_modules)
    assert "App\\Models\\User" in modules
    assert "App\\Services\\AuthService" in modules


def test_php_stdlib_filtered(php_lang):
    meta = extract_file_metadata(_PHP_USE_CODE, php_lang)
    # Illuminate imports should be filtered by stdlib_prefixes
    filtered_modules = set(meta.imported_modules)
    assert "Illuminate\\Http\\Request" not in filtered_modules
    assert "Illuminate\\Support\\Facades\\Cache" not in filtered_modules


def test_php_project_imports_kept(php_lang):
    meta = extract_file_metadata(_PHP_USE_CODE, php_lang)
    modules = set(meta.imported_modules)
    assert "App\\Models\\User" in modules
    assert "App\\Services\\AuthService" in modules


# ---------------------------------------------------------------------------
# PHP trait extraction
# ---------------------------------------------------------------------------

_PHP_TRAIT_CODE = """\
<?php

trait HasSlug {
    public function generateSlug(): string {
        return strtolower($this->name);
    }
}

trait Cacheable {
    public function cacheKey(): string {
        return static::class . ':' . $this->id;
    }
}
"""


def test_php_traits_extracted(php_lang):
    meta = extract_file_metadata(_PHP_TRAIT_CODE, php_lang)
    defs = meta.class_function_defs
    class_defs = [d for d in defs if d.startswith("class ")]
    assert "class HasSlug" in class_defs
    assert "class Cacheable" in class_defs


# ---------------------------------------------------------------------------
# PHP enum extraction
# ---------------------------------------------------------------------------

_PHP_ENUM_CODE = """\
<?php

enum Status: string {
    case Active = 'active';
    case Inactive = 'inactive';
}

enum Priority: int {
    case Low = 1;
    case High = 10;
}
"""


def test_php_enums_extracted(php_lang):
    meta = extract_file_metadata(_PHP_ENUM_CODE, php_lang)
    defs = meta.class_function_defs
    class_defs = [d for d in defs if d.startswith("class ")]
    assert "class Status" in class_defs
    assert "class Priority" in class_defs


# ---------------------------------------------------------------------------
# PHP combined extraction
# ---------------------------------------------------------------------------

_PHP_FULL_CODE = """\
<?php

namespace App\\Http\\Controllers;

use App\\Models\\User;
use Illuminate\\Http\\Request;

class UserController extends Controller {
    public function index(Request $request) {
        return User::all();
    }

    public function store(Request $request) {
        return User::create($request->all());
    }
}

interface Searchable {
    public function search(string $query): array;
}

function helper_function($arg1, $arg2) {
    return $arg1 + $arg2;
}
"""


def test_php_classes_extracted(php_lang):
    meta = extract_file_metadata(_PHP_FULL_CODE, php_lang)
    class_defs = [d for d in meta.class_function_defs if d.startswith("class ")]
    assert "class UserController" in class_defs
    assert "class Searchable" in class_defs


def test_php_methods_extracted(php_lang):
    meta = extract_file_metadata(_PHP_FULL_CODE, php_lang)
    func_defs = [d for d in meta.class_function_defs if d.startswith("def ")]
    func_names = [d.split("(")[0] for d in func_defs]
    assert "def index" in func_names
    assert "def store" in func_names
    assert "def helper_function" in func_names


# ---------------------------------------------------------------------------
# PHP trait in definition nodes (chunker)
# ---------------------------------------------------------------------------


def test_php_trait_in_definition_nodes(php_lang):
    nodes = get_definition_nodes(_PHP_TRAIT_CODE, php_lang)
    names = [n[2] for n in nodes]
    assert "HasSlug" in names


# ---------------------------------------------------------------------------
# Ruby require extraction
# ---------------------------------------------------------------------------

_RUBY_REQUIRE_CODE = """\
require 'json'
require 'active_record'
require_relative '../models/user'
require_relative 'helpers/auth_helper'
"""


def test_ruby_require_extracted(ruby_lang):
    meta = extract_file_metadata(_RUBY_REQUIRE_CODE, ruby_lang)
    assert len(meta.imports) > 0, "No imports extracted from Ruby require statements"


def test_ruby_require_captures_module_paths(ruby_lang):
    meta = extract_file_metadata(_RUBY_REQUIRE_CODE, ruby_lang)
    modules = set(meta.imported_modules)
    # require_relative paths should be captured
    assert "../models/user" in modules
    assert "helpers/auth_helper" in modules


def test_ruby_stdlib_filtered(ruby_lang):
    meta = extract_file_metadata(_RUBY_REQUIRE_CODE, ruby_lang)
    modules = set(meta.imported_modules)
    assert "json" not in modules


def test_ruby_project_imports_kept(ruby_lang):
    meta = extract_file_metadata(_RUBY_REQUIRE_CODE, ruby_lang)
    modules = set(meta.imported_modules)
    assert "active_record" in modules
    assert "../models/user" in modules


# ---------------------------------------------------------------------------
# Ruby class/module/method extraction (existing)
# ---------------------------------------------------------------------------

_RUBY_CLASS_CODE = """\
module MyApp
  class UserService
    def initialize(repo)
      @repo = repo
    end

    def find_user(id)
      @repo.find(id)
    end
  end
end
"""


def test_ruby_module_extracted(ruby_lang):
    meta = extract_file_metadata(_RUBY_CLASS_CODE, ruby_lang)
    class_defs = [d for d in meta.class_function_defs if d.startswith("class ")]
    assert "class MyApp" in class_defs
    assert "class UserService" in class_defs


def test_ruby_methods_extracted(ruby_lang):
    meta = extract_file_metadata(_RUBY_CLASS_CODE, ruby_lang)
    func_defs = [d for d in meta.class_function_defs if d.startswith("def ")]
    func_names = [d.split("(")[0] for d in func_defs]
    assert "def initialize" in func_names
    assert "def find_user" in func_names
