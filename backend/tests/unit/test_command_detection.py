"""Tests for auto-detection of lint, format, and test commands.

Pure unit tests — no LLM, no network calls required.
"""

import json

from lean_ai.context.command_detection import (
    detect_commands,
    load_commands_json,
    write_commands_json,
)

# ── PHP ──


class TestDetectPHP:
    def test_laravel_with_pint(self, tmp_path):
        (tmp_path / "composer.json").write_text(json.dumps({
            "require": {"php": "^8.3", "laravel/framework": "^12.0"},
            "require-dev": {
                "laravel/pint": "^1.0",
                "phpunit/phpunit": "^11.0",
            },
        }))
        (tmp_path / "artisan").touch()
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "php -l ."
        assert cmds["format"] == "./vendor/bin/pint"
        assert cmds["test"] == "php artisan test"

    def test_phpunit_without_artisan(self, tmp_path):
        (tmp_path / "composer.json").write_text(json.dumps({
            "require": {"php": "^8.3"},
            "require-dev": {"phpunit/phpunit": "^11.0"},
        }))
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "php -l ."
        assert cmds["test"] == "./vendor/bin/phpunit"
        assert cmds["format"] == ""

    def test_no_test_framework(self, tmp_path):
        (tmp_path / "composer.json").write_text(json.dumps({
            "require": {"php": "^8.3"},
        }))
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "php -l ."
        assert cmds["test"] == ""


# ── Python ──


class TestDetectPython:
    def test_ruff_in_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi"]\n'
            "[tool.ruff]\nline-length = 88\n"
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "ruff check ."
        assert cmds["lint_fix"] == "ruff check --fix ."
        assert cmds["format"] == "ruff format ."

    def test_pytest_in_deps(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["pytest>=7.0"]\n'
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "pytest"

    def test_black_and_flake8(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(
            "flask\nblack\nflake8\npytest\n"
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "flake8"
        assert cmds["format"] == "black ."
        assert cmds["test"] == "pytest"

    def test_ruff_in_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("ruff\npytest\n")
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "ruff check ."
        assert cmds["format"] == "ruff format ."


# ── Node / TypeScript ──


class TestDetectNode:
    def test_scripts_from_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {
                "lint": "eslint src/",
                "format": "prettier --write .",
                "test": "jest --coverage",
            },
        }))
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "npm run lint"
        assert cmds["format"] == "npm run format"
        assert cmds["test"] == "npm run test"

    def test_pnpm_detection(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "vitest run"},
        }))
        (tmp_path / "pnpm-lock.yaml").touch()
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "pnpm test"

    def test_yarn_detection(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "scripts": {"test": "jest"},
        }))
        (tmp_path / "yarn.lock").touch()
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "yarn test"

    def test_devdeps_fallback(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {
                "eslint": "^8.0",
                "prettier": "^3.0",
                "jest": "^29.0",
            },
        }))
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "npx eslint ."
        assert cmds["format"] == "npx prettier --write ."
        assert cmds["test"] == "npx jest"

    def test_vitest_detection(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "devDependencies": {"vitest": "^1.0"},
        }))
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "npx vitest run"


# ── Ruby ──


class TestDetectRuby:
    def test_rails_with_rubocop(self, tmp_path):
        (tmp_path / "Gemfile").write_text(
            'source "https://rubygems.org"\n'
            'gem "rails"\n'
            'gem "rubocop", group: :development\n'
        )
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "rails").touch()
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "bundle exec rubocop"
        assert cmds["lint_fix"] == "bundle exec rubocop -a"
        assert cmds["test"] == "bin/rails test"

    def test_rspec(self, tmp_path):
        (tmp_path / "Gemfile").write_text(
            'gem "sinatra"\ngem "rspec"\n'
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "bundle exec rspec"

    def test_minitest_fallback(self, tmp_path):
        (tmp_path / "Gemfile").write_text('gem "sinatra"\n')
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "bundle exec rake test"


# ── Go ──


class TestDetectGo:
    def test_go_project(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module example.com/app\n\ngo 1.22\n"
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "go vet ./..."
        assert cmds["format"] == "gofmt -w ."
        assert cmds["test"] == "go test ./..."


# ── Rust ──


class TestDetectRust:
    def test_cargo_project(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "app"\nedition = "2021"\n'
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["lint"] == "cargo clippy"
        assert cmds["lint_fix"] == "cargo clippy --fix --allow-dirty"
        assert cmds["format"] == "cargo fmt"
        assert cmds["test"] == "cargo test"


# ── Java ──


class TestDetectJava:
    def test_maven(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "mvn test"

    def test_gradle(self, tmp_path):
        (tmp_path / "build.gradle").write_text("apply plugin: 'java'")
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "gradle test"

    def test_gradle_kotlin(self, tmp_path):
        (tmp_path / "build.gradle.kts").write_text(
            'plugins { id("java") }'
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["test"] == "gradle test"


# ── C# ──


class TestDetectCSharp:
    def test_csproj(self, tmp_path):
        (tmp_path / "MyApp.csproj").write_text(
            "<Project></Project>"
        )
        cmds = detect_commands(str(tmp_path))
        assert cmds["format"] == "dotnet format"
        assert cmds["lint"] == "dotnet build"
        assert cmds["test"] == "dotnet test"


# ── Persistence ──


class TestPersistence:
    def test_write_and_load(self, tmp_path):
        cmds = {
            "format": "ruff format .",
            "lint": "ruff check .",
            "lint_fix": "",
            "test": "pytest",
        }
        path = write_commands_json(str(tmp_path), cmds)
        assert path.endswith("commands.json")
        loaded = load_commands_json(str(tmp_path))
        assert loaded == cmds

    def test_load_missing_file(self, tmp_path):
        loaded = load_commands_json(str(tmp_path))
        assert loaded == {}

    def test_load_malformed_json(self, tmp_path):
        lean_dir = tmp_path / ".lean_ai"
        lean_dir.mkdir()
        (lean_dir / "commands.json").write_text("not json{")
        loaded = load_commands_json(str(tmp_path))
        assert loaded == {}


# ── No project files ──


class TestNoFiles:
    def test_empty_directory(self, tmp_path):
        cmds = detect_commands(str(tmp_path))
        assert all(v == "" for v in cmds.values())
