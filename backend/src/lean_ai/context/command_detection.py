"""Auto-detect lint, format, and test commands from project files.

Scans dependency and config files to determine the correct commands for
post-execution validation.  Pure file I/O — no LLM calls, no network.

Detection order: PHP → Python → Node/TS → Ruby → Go → Rust → Java → C#.
First ecosystem that returns non-empty values wins (projects rarely mix
ecosystems at the root level).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Keys returned by every detector
_EMPTY: dict[str, str] = {"format": "", "lint_fix": "", "lint": "", "test": ""}


# ── Public API ──────────────────────────────────────────────────────


def detect_commands(repo_root: str) -> dict[str, str]:
    """Detect lint/format/test commands from project files.

    Returns dict with keys: ``format``, ``lint_fix``, ``lint``, ``test``.
    Undetected commands have empty-string values.
    """
    root = Path(repo_root)
    detectors = [
        _detect_php_commands,
        _detect_python_commands,
        _detect_node_commands,
        _detect_ruby_commands,
        _detect_go_commands,
        _detect_rust_commands,
        _detect_java_commands,
        _detect_csharp_commands,
    ]

    result = dict(_EMPTY)
    for detector in detectors:
        try:
            detected = detector(root)
        except Exception as exc:
            logger.debug("Command detector %s failed: %s", detector.__name__, exc)
            continue
        # Merge — first detector to set a key wins
        for key in _EMPTY:
            if not result[key] and detected.get(key):
                result[key] = detected[key]

    return result


def write_commands_json(repo_root: str, commands: dict[str, str]) -> str:
    """Write commands to ``.lean_ai/commands.json``.  Returns file path."""
    lean_dir = Path(repo_root) / ".lean_ai"
    lean_dir.mkdir(parents=True, exist_ok=True)
    path = lean_dir / "commands.json"
    path.write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")
    return str(path)


def load_commands_json(repo_root: str) -> dict[str, str]:
    """Load commands from ``.lean_ai/commands.json``.

    Returns empty dict if the file does not exist or is malformed.
    """
    path = Path(repo_root) / ".lean_ai" / "commands.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception:
        pass
    return {}


# ── Ecosystem detectors ─────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    """Read a JSON file, returning empty dict on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _file_contains(path: Path, needle: str) -> bool:
    """Check if a text file contains a substring (case-insensitive)."""
    try:
        return needle.lower() in path.read_text(encoding="utf-8").lower()
    except Exception:
        return False


# ── PHP ──


def _detect_php_commands(root: Path) -> dict[str, str]:
    composer = root / "composer.json"
    if not composer.is_file():
        return _EMPTY

    data = _read_json(composer)
    result = dict(_EMPTY)

    # Lint — php -l supports directory-level linting in PHP 8.3+
    result["lint"] = "php -l ."

    # Format — Laravel Pint
    require_dev = data.get("require-dev", {})
    if "laravel/pint" in require_dev:
        result["format"] = "./vendor/bin/pint"

    # Test — artisan or phpunit
    if (root / "artisan").is_file():
        result["test"] = "php artisan test"
    elif "phpunit/phpunit" in require_dev:
        result["test"] = "./vendor/bin/phpunit"

    return result


# ── Python ──


def _detect_python_commands(root: Path) -> dict[str, str]:
    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"

    if not pyproject.is_file() and not requirements.is_file():
        return _EMPTY

    result = dict(_EMPTY)

    # Check pyproject.toml for tool configs and dependencies
    has_ruff = False
    has_black = False
    has_flake8 = False
    has_pytest = False

    if pyproject.is_file():
        content = pyproject.read_text(encoding="utf-8")
        lower = content.lower()

        # Tool sections
        has_ruff = "[tool.ruff]" in lower or '"ruff"' in lower
        has_black = "[tool.black]" in lower or '"black"' in lower
        has_flake8 = '"flake8"' in lower

        # Dependencies (both [project] and [tool.poetry])
        has_pytest = '"pytest' in lower or "'pytest" in lower

    # Fallback: check requirements.txt
    if requirements.is_file() and not (has_ruff or has_pytest):
        req_content = requirements.read_text(encoding="utf-8").lower()
        if not has_ruff and "ruff" in req_content:
            has_ruff = True
        if not has_black and "black" in req_content:
            has_black = True
        if not has_flake8 and "flake8" in req_content:
            has_flake8 = True
        if not has_pytest and "pytest" in req_content:
            has_pytest = True

    # Lint
    if has_ruff:
        result["lint"] = "ruff check ."
        result["lint_fix"] = "ruff check --fix ."
        result["format"] = "ruff format ."
    elif has_flake8:
        result["lint"] = "flake8"
    if not result["format"] and has_black:
        result["format"] = "black ."

    # Test
    if has_pytest:
        result["test"] = "pytest"

    return result


# ── Node / TypeScript ──


def _detect_package_manager(root: Path) -> str:
    """Detect Node package manager: pnpm, yarn, or npm."""
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _node_run_cmd(pm: str, script_name: str) -> str:
    """Build the run command for a package.json script."""
    if pm == "npm":
        return f"npm run {script_name}"
    return f"{pm} {script_name}"


def _detect_node_commands(root: Path) -> dict[str, str]:
    pkg_json = root / "package.json"
    if not pkg_json.is_file():
        return _EMPTY

    data = _read_json(pkg_json)
    result = dict(_EMPTY)
    pm = _detect_package_manager(root)
    scripts = data.get("scripts", {})
    dev_deps = data.get("devDependencies", {})
    deps = data.get("dependencies", {})
    all_deps = {**deps, **dev_deps}

    # Prefer package.json scripts (most reliable)
    if "lint" in scripts:
        result["lint"] = _node_run_cmd(pm, "lint")
    if "lint:fix" in scripts:
        result["lint_fix"] = _node_run_cmd(pm, "lint:fix")
    if "format" in scripts:
        result["format"] = _node_run_cmd(pm, "format")
    if "test" in scripts:
        result["test"] = _node_run_cmd(pm, "test")

    # Fallback: detect from devDependencies
    if not result["lint"] and "eslint" in all_deps:
        result["lint"] = "npx eslint ."
    if not result["format"] and "prettier" in all_deps:
        result["format"] = "npx prettier --write ."
    if not result["test"]:
        if "jest" in all_deps:
            result["test"] = "npx jest"
        elif "vitest" in all_deps:
            result["test"] = "npx vitest run"
        elif "mocha" in all_deps:
            result["test"] = "npx mocha"

    return result


# ── Ruby ──


def _detect_ruby_commands(root: Path) -> dict[str, str]:
    gemfile = root / "Gemfile"
    if not gemfile.is_file():
        return _EMPTY

    result = dict(_EMPTY)
    content = gemfile.read_text(encoding="utf-8").lower()

    # Lint — rubocop
    if "rubocop" in content:
        result["lint"] = "bundle exec rubocop"
        result["lint_fix"] = "bundle exec rubocop -a"

    # Test — Rails or RSpec or minitest
    if (root / "bin" / "rails").is_file():
        result["test"] = "bin/rails test"
    elif "rspec" in content:
        result["test"] = "bundle exec rspec"
    else:
        result["test"] = "bundle exec rake test"

    return result


# ── Go ──


def _detect_go_commands(root: Path) -> dict[str, str]:
    if not (root / "go.mod").is_file():
        return _EMPTY

    return {
        "format": "gofmt -w .",
        "lint_fix": "",
        "lint": "go vet ./...",
        "test": "go test ./...",
    }


# ── Rust ──


def _detect_rust_commands(root: Path) -> dict[str, str]:
    if not (root / "Cargo.toml").is_file():
        return _EMPTY

    return {
        "format": "cargo fmt",
        "lint_fix": "cargo clippy --fix --allow-dirty",
        "lint": "cargo clippy",
        "test": "cargo test",
    }


# ── Java ──


def _detect_java_commands(root: Path) -> dict[str, str]:
    result = dict(_EMPTY)

    if (root / "pom.xml").is_file():
        result["test"] = "mvn test"
    elif (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        result["test"] = "gradle test"

    return result


# ── C# ──


def _detect_csharp_commands(root: Path) -> dict[str, str]:
    # Check for any .csproj file
    csproj_files = list(root.glob("*.csproj")) + list(root.glob("**/*.csproj"))
    if not csproj_files:
        return _EMPTY

    return {
        "format": "dotnet format",
        "lint_fix": "",
        "lint": "dotnet build",
        "test": "dotnet test",
    }
