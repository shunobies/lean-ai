"""Tests for fan-in resolution strategies in metadata extraction."""

from lean_ai.context.metadata import _RepoMetadata, _resolve_fan_in
from lean_ai.languages.definitions import FileMetadata

# ---------------------------------------------------------------------------
# backslash_to_slash fan-in (PHP namespaces)
# ---------------------------------------------------------------------------


def test_backslash_to_slash_exact_match():
    """App\\Models\\User -> App/Models/User.php"""
    metadata = _RepoMetadata()
    metadata.files = {
        "app/Http/Controllers/UserController.php": FileMetadata(
            imported_modules=["App\\Models\\User"],
        ),
    }
    file_paths = {
        "app/Http/Controllers/UserController.php",
        "App/Models/User.php",  # exact case match
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert fan_in.get("App/Models/User.php", 0) == 1


def test_backslash_to_slash_lowercase_first_segment():
    """App\\Models\\User -> app/Models/User.php (PSR-4 lowercase)"""
    metadata = _RepoMetadata()
    metadata.files = {
        "app/Http/Controllers/UserController.php": FileMetadata(
            imported_modules=["App\\Models\\User", "App\\Services\\AuthService"],
        ),
    }
    file_paths = {
        "app/Http/Controllers/UserController.php",
        "app/Models/User.php",  # lowercase 'app'
        "app/Services/AuthService.php",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert fan_in.get("app/Models/User.php", 0) == 1
    assert fan_in.get("app/Services/AuthService.php", 0) == 1


def test_backslash_to_slash_multiple_importers():
    """Multiple files importing the same module should increment fan-in."""
    metadata = _RepoMetadata()
    metadata.files = {
        "app/Http/Controllers/UserController.php": FileMetadata(
            imported_modules=["App\\Models\\User"],
        ),
        "app/Http/Controllers/AdminController.php": FileMetadata(
            imported_modules=["App\\Models\\User"],
        ),
        "app/Services/UserService.php": FileMetadata(
            imported_modules=["App\\Models\\User"],
        ),
    }
    file_paths = {
        "app/Http/Controllers/UserController.php",
        "app/Http/Controllers/AdminController.php",
        "app/Services/UserService.php",
        "app/Models/User.php",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert fan_in.get("app/Models/User.php", 0) == 3


def test_backslash_to_slash_with_source_prefix():
    """Fan-in should work when files are under a source prefix."""
    metadata = _RepoMetadata()
    metadata.files = {
        "src/app/Http/Controllers/UserController.php": FileMetadata(
            imported_modules=["App\\Models\\User"],
        ),
    }
    file_paths = {
        "src/app/Http/Controllers/UserController.php",
        "src/app/Models/User.php",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, ["src/"])
    assert fan_in.get("src/app/Models/User.php", 0) == 1


# ---------------------------------------------------------------------------
# relative_path fan-in (TypeScript/JavaScript)
# ---------------------------------------------------------------------------


def test_relative_path_ts_import():
    """./utils resolves to utils.ts in the same directory."""
    metadata = _RepoMetadata()
    metadata.files = {
        "src/components/App.ts": FileMetadata(
            imported_modules=["./utils"],
        ),
    }
    file_paths = {
        "src/components/App.ts",
        "src/components/utils.ts",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert fan_in.get("src/components/utils.ts", 0) == 1


def test_relative_path_parent_dir():
    """../models/user resolves to models/user.ts in parent directory."""
    metadata = _RepoMetadata()
    metadata.files = {
        "src/components/UserList.ts": FileMetadata(
            imported_modules=["../models/user"],
        ),
    }
    file_paths = {
        "src/components/UserList.ts",
        "src/models/user.ts",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert fan_in.get("src/models/user.ts", 0) == 1


def test_relative_path_index_file():
    """./components resolves to components/index.ts via package markers."""
    metadata = _RepoMetadata()
    metadata.files = {
        "src/App.ts": FileMetadata(
            imported_modules=["./components"],
        ),
    }
    file_paths = {
        "src/App.ts",
        "src/components/index.ts",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert fan_in.get("src/components/index.ts", 0) == 1


def test_relative_path_ignores_bare_modules():
    """Non-relative imports (bare module names) should be ignored."""
    metadata = _RepoMetadata()
    metadata.files = {
        "src/App.ts": FileMetadata(
            imported_modules=["react", "lodash"],
        ),
    }
    file_paths = {
        "src/App.ts",
    }
    fan_in = _resolve_fan_in(metadata, file_paths, [""])
    assert len(fan_in) == 0
