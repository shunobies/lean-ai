"""Data classes for language definitions — tree-sitter based."""

from dataclasses import dataclass, field


@dataclass
class FileTestPatterns:
    """Patterns to identify test files."""

    directories: list[str] = field(default_factory=list)
    file_prefixes: list[str] = field(default_factory=list)
    file_suffixes: list[str] = field(default_factory=list)


@dataclass
class FanInConfig:
    """How to resolve import paths to file paths for fan-in ranking."""

    strategy: str = "none"  # "dot_to_slash", "relative_path", "none"
    suffix: str = ""
    package_markers: list[str] = field(default_factory=list)


@dataclass
class FileMetadata:
    """Metadata extracted from a single source file via tree-sitter."""

    class_function_defs: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    imported_modules: list[str] = field(default_factory=list)


@dataclass
class LanguageDefinition:
    """Complete definition of a supported programming language."""

    name: str
    extensions: list[str]
    # tree-sitter grammar module name (e.g. "tree_sitter_python")
    ts_grammar: str
    # tree-sitter query strings for extracting definitions
    ts_class_query: str = ""
    ts_function_query: str = ""
    ts_import_query: str = ""
    # Non-extraction config
    stdlib_prefixes: list[str] = field(default_factory=list)
    fan_in: FanInConfig = field(default_factory=FanInConfig)
    test_patterns: FileTestPatterns = field(default_factory=FileTestPatterns)
    key_files: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
