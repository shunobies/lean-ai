"""Language registry — loads YAML definitions and provides lookup by extension.

Each YAML file defines a language with tree-sitter query strings (no regex).
The registry compiles queries at first use and caches the results.
"""

import logging
from functools import lru_cache
from pathlib import Path

from ruamel.yaml import YAML

from lean_ai.languages.definitions import (
    FanInConfig,
    FileTestPatterns,
    LanguageDefinition,
)

logger = logging.getLogger(__name__)


def _parse_language_yaml(data: dict) -> LanguageDefinition:
    """Convert a raw YAML dict into a LanguageDefinition."""
    fan_in_data = data.get("fan_in") or {}
    test_data = data.get("test_patterns") or {}

    return LanguageDefinition(
        name=data["name"],
        extensions=data.get("extensions", []),
        ts_grammar=data.get("ts_grammar", ""),
        ts_class_query=data.get("ts_class_query", ""),
        ts_function_query=data.get("ts_function_query", ""),
        ts_import_query=data.get("ts_import_query", ""),
        stdlib_prefixes=data.get("stdlib_prefixes", []),
        fan_in=FanInConfig(
            strategy=fan_in_data.get("strategy", "none"),
            suffix=fan_in_data.get("suffix", ""),
            package_markers=fan_in_data.get("package_markers", []),
        ),
        test_patterns=FileTestPatterns(
            directories=test_data.get("directories", []),
            file_prefixes=test_data.get("file_prefixes", []),
            file_suffixes=test_data.get("file_suffixes", []),
        ),
        key_files=data.get("key_files", []),
        entry_points=data.get("entry_points", []),
    )


class LanguageRegistry:
    """Singleton registry of language definitions loaded from YAML."""

    def __init__(self, languages_dir: Path):
        self._by_extension: dict[str, LanguageDefinition] = {}
        self._all: list[LanguageDefinition] = []
        self._load(languages_dir)

    def _load(self, languages_dir: Path) -> None:
        if not languages_dir.is_dir():
            logger.warning("Languages directory not found: %s", languages_dir)
            return

        yaml = YAML()
        for yaml_path in sorted(languages_dir.glob("*.yaml")):
            try:
                data = yaml.load(yaml_path)
                if data is None:
                    continue
                lang = _parse_language_yaml(data)
                self._all.append(lang)
                for ext in lang.extensions:
                    self._by_extension[ext] = lang
            except Exception as exc:
                logger.warning("Failed to load language from %s: %s", yaml_path, exc)

        logger.info(
            "Loaded %d language definitions (%d extensions)",
            len(self._all), len(self._by_extension),
        )

    def get_language(self, ext: str) -> LanguageDefinition | None:
        """Look up a language by file extension (e.g. '.py')."""
        return self._by_extension.get(ext)

    def all_source_extensions(self) -> set[str]:
        """All registered file extensions."""
        return set(self._by_extension.keys())

    def all_key_files(self) -> list[str]:
        """All language-specific key files."""
        files: list[str] = []
        for lang in self._all:
            files.extend(lang.key_files)
        return files

    def all_entry_points(self) -> list[str]:
        """All language-specific entry point filenames."""
        points: list[str] = []
        for lang in self._all:
            points.extend(lang.entry_points)
        return points

    def all_languages(self) -> list[LanguageDefinition]:
        return list(self._all)

    def is_test_file(self, path: str) -> bool:
        """Check if a file path matches test file patterns."""
        parts = path.replace("\\", "/").split("/")

        for lang in self._all:
            tp = lang.test_patterns
            # Check directory patterns
            for d in tp.directories:
                if d in parts:
                    return True
            # Check file name patterns
            filename = parts[-1] if parts else ""
            for prefix in tp.file_prefixes:
                if filename.startswith(prefix):
                    return True
            for suffix in tp.file_suffixes:
                if filename.endswith(suffix):
                    return True

        return False


@lru_cache(maxsize=1)
def get_registry() -> LanguageRegistry:
    """Get or create the singleton language registry."""
    from lean_ai.config import settings
    return LanguageRegistry(settings.languages_dir)
