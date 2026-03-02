"""Tree-sitter based metadata extraction engine.

Parses source files using tree-sitter grammars and extracts:
- Class/function/method definitions
- Import statements
- Imported module names (for fan-in calculation)

No regex — all extraction uses AST node traversal.
"""

import logging
from functools import lru_cache

import tree_sitter

from lean_ai.languages.definitions import FileMetadata, LanguageDefinition

logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _get_parser(grammar_module: str) -> tree_sitter.Parser | None:
    """Load and cache a tree-sitter parser for a given grammar."""
    try:
        mod = __import__(grammar_module)
        lang = tree_sitter.Language(mod.language())
        parser = tree_sitter.Parser(lang)
        return parser
    except Exception as e:
        logger.warning("Failed to load tree-sitter grammar %s: %s", grammar_module, e)
        return None


@lru_cache(maxsize=64)
def _compile_query(grammar_module: str, query_str: str) -> tree_sitter.Query | None:
    """Compile and cache a tree-sitter query."""
    if not query_str.strip():
        return None
    try:
        mod = __import__(grammar_module)
        lang = tree_sitter.Language(mod.language())
        return tree_sitter.Query(lang, query_str)
    except Exception as e:
        logger.warning("Failed to compile query for %s: %s", grammar_module, e)
        return None


def _query_matches(query: tree_sitter.Query, node: tree_sitter.Node) -> list[tuple]:
    """Run a query against a node using QueryCursor (tree-sitter 0.25+ API)."""
    cursor = tree_sitter.QueryCursor(query)
    return list(cursor.matches(node))


def _node_text(node: tree_sitter.Node, source: bytes) -> str:
    """Extract the text content of a tree-sitter node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def extract_file_metadata(text: str, lang: LanguageDefinition) -> FileMetadata:
    """Extract metadata from a source file using tree-sitter AST queries.

    Falls back to empty metadata if the grammar isn't available.
    """
    meta = FileMetadata()
    parser = _get_parser(lang.ts_grammar)
    if parser is None:
        return meta

    source = text.encode("utf-8")
    tree = parser.parse(source)

    # Extract class/function definitions
    if lang.ts_class_query:
        query = _compile_query(lang.ts_grammar, lang.ts_class_query)
        if query:
            for match in _query_matches(query, tree.root_node):
                _process_definition_match(match, source, meta, "class")

    if lang.ts_function_query:
        query = _compile_query(lang.ts_grammar, lang.ts_function_query)
        if query:
            for match in _query_matches(query, tree.root_node):
                _process_definition_match(match, source, meta, "function")

    # Extract imports
    if lang.ts_import_query:
        query = _compile_query(lang.ts_grammar, lang.ts_import_query)
        if query:
            for match in _query_matches(query, tree.root_node):
                _process_import_match(match, source, meta, lang)

    return meta


def _process_definition_match(
    match: tuple, source: bytes, meta: FileMetadata, kind: str,
) -> None:
    """Process a tree-sitter match for a class/function definition."""
    _, captures = match
    name_node = None
    params_text = ""

    for capture_name, nodes in captures.items():
        if not nodes:
            continue
        node = nodes[0] if isinstance(nodes, list) else nodes
        if capture_name == "name":
            name_node = node
        elif capture_name == "params":
            params_text = _node_text(node, source)

    if name_node:
        name = _node_text(name_node, source)
        if kind == "class":
            meta.class_function_defs.append(f"class {name}")
        elif params_text:
            meta.class_function_defs.append(f"def {name}({params_text})")
        else:
            meta.class_function_defs.append(f"def {name}()")


def _process_import_match(
    match: tuple, source: bytes, meta: FileMetadata, lang: LanguageDefinition,
) -> None:
    """Process a tree-sitter match for an import statement."""
    _, captures = match
    module_node = None

    for capture_name, nodes in captures.items():
        if not nodes:
            continue
        node = nodes[0] if isinstance(nodes, list) else nodes
        if capture_name == "module":
            module_node = node

    if module_node:
        module_name = _node_text(module_node, source)
        # Filter stdlib
        is_stdlib = any(module_name.startswith(prefix) for prefix in lang.stdlib_prefixes)
        if not is_stdlib:
            meta.imports.append(module_name)
            meta.imported_modules.append(module_name)


def get_definition_nodes(text: str, lang: LanguageDefinition) -> list[tuple[int, int, str]]:
    """Get line ranges of top-level definitions for AST-aware chunking.

    Returns list of (start_line, end_line, name) tuples.
    Used by the chunker to split files at function/class boundaries.
    """
    parser = _get_parser(lang.ts_grammar)
    if parser is None:
        return []

    source = text.encode("utf-8")
    tree = parser.parse(source)
    boundaries: list[tuple[int, int, str]] = []

    # Walk top-level children looking for definition nodes
    for child in tree.root_node.children:
        if child.type in _DEFINITION_NODE_TYPES:
            name = ""
            for sub in child.children:
                if sub.type in ("identifier", "name", "property_identifier"):
                    name = _node_text(sub, source)
                    break
            boundaries.append((
                child.start_point.row + 1,  # 1-based
                child.end_point.row + 1,
                name or child.type,
            ))

    return boundaries


# Node types that represent top-level definitions across languages
_DEFINITION_NODE_TYPES = frozenset({
    # Python
    "function_definition", "class_definition", "decorated_definition",
    # JavaScript/TypeScript
    "function_declaration", "class_declaration", "export_statement",
    "lexical_declaration", "variable_declaration",
    # Java/C#
    "method_declaration", "class_declaration", "interface_declaration",
    "enum_declaration",
    # Go
    "function_declaration", "method_declaration", "type_declaration",
    # Rust
    "function_item", "struct_item", "impl_item", "enum_item", "trait_item",
    "mod_item",
    # Ruby
    "method", "class", "module",
    # C/C++
    "function_definition", "struct_specifier", "class_specifier",
    "declaration",
    # PHP
    "function_definition", "class_declaration", "method_declaration",
})
