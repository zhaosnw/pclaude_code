"""
Pure parser facade compatible with tree-sitter-bash node shape, plus real
tree-sitter integration when the library is available.

Port of: src/utils/bash/bashParser.ts (types, keywords, module API, AST queries).

The TypeScript implementation contains a full lexer/parser; the Python build
uses a native/stub :func:`parse_source` hook when available.  When the
``tree-sitter`` and ``tree-sitter-bash`` packages are installed this module
delegates to them automatically; otherwise it provides a pure-Python fallback
that produces compatible :class:`TsNode` trees via regex tokenisation.

Architecture
------------
* :class:`BashParser` — singleton-managed parser with lazy language loading.
* :class:`TsNode` — AST node matching tree-sitter's representation (type, text,
  byte offsets, children).  Includes convenience methods for traversal.
* Query API — :func:`query_captures` / :func:`query_matches` wrap tree-sitter
  queries and degrade gracefully.
* Structured results — :func:`parse_bash_command` returns a
  :class:`BashParseResult` with the root node plus extracted metadata.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal, Optional, Protocol, Sequence, overload

# ---------------------------------------------------------------------------
# Re-export symbols expected by the rest of the codebase
# ---------------------------------------------------------------------------

__all__ = [
    "PARSE_ABORTED",
    "TsNode",
    "ParserModule",
    "SHELL_KEYWORDS",
    "ensure_parser_initialized",
    "get_parser_module",
    "set_parser_module",
    "parse_source",
    "parse_command_raw",
    # -- public API --
    "get_bash_parser",
    "reset_bash_parser",
    "BashParser",
    "BashParseResult",
    "CommandInfo",
    "RedirectInfo",
    "ExpansionInfo",
    "query_captures",
    "query_matches",
    "parse_bash_command",
    "parse_bash_command_sync",
    "check_bash_syntax",
    "is_bash_available",
    "walk_tree",
    "find_nodes_of_type",
    "get_child_by_type",
    "get_children_by_type",
    "ts_node_to_dict",
    "ts_node_from_dict",
    "ts_node_to_json",
    "dump_tree",
    # -- node type predicates --
    "is_command_like_node",
    "is_control_flow_node",
    "is_redirect_node",
    "is_string_node",
    "is_expansion_node",
    "is_operator_node",
    "is_comment_node",
    "is_error_node",
    "is_list_node",
    # -- structural helpers --
    "split_command_outside_quotes",
    "contains_unclosed_quotes",
    "balanced_brackets",
]


# ---------------------------------------------------------------------------
# Sentinels & constants
# ---------------------------------------------------------------------------

class _ParseAbortedType:
    __slots__ = ()


PARSE_ABORTED = _ParseAbortedType()


SHELL_KEYWORDS: frozenset[str] = frozenset({
    "if", "then", "elif", "else", "fi",
    "while", "until", "for", "in", "do", "done",
    "case", "esac", "function", "select",
})

_DECL_KEYWORDS: frozenset[str] = frozenset(
    {"export", "declare", "typeset", "readonly", "local"}
)

# shell metacharacters that signal complex commands
_SHELL_METACHARS: set[str] = {
    "|", "&", ";", "<", ">", "(", ")", "$", "`", "\\", '"', "'", " ", "\t", "\n",
    "*", "?", "[", "]", "!", "~", "#",
}

_PARSE_TIMEOUT_MS = 50.0

# Max source length we attempt to parse with tree-sitter (256 KiB)
_MAX_SOURCE_LENGTH = 256 * 1024


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _time() -> float:
    """Monotonic clock in milliseconds (approximates TS ``Date.now()``)."""
    return time.monotonic() * 1000.0


# ---------------------------------------------------------------------------
# TsNode — tree-sitter-compatible AST node
# ---------------------------------------------------------------------------

@dataclass
class TsNode:
    """AST node (UTF-8 byte offsets for start/end, matching tree-sitter).

    Fields match the tree-sitter ``SyntaxNode`` shape:
      ``type``, ``text``, ``start_index``, ``end_index``, ``children``.

    Also carries optional ``is_named`` and ``has_error`` flags for
    compatibility with tree-sitter's named/anonymous node distinction.
    """

    type: str
    text: str
    start_index: int
    end_index: int
    children: list[TsNode] = field(default_factory=list)
    is_named: bool = True
    has_error: bool = False

    # -- traversal helpers --

    @property
    def child_count(self) -> int:
        """Number of direct children."""
        return len(self.children)

    @property
    def is_error(self) -> bool:
        """True when this node (or any descendant) is an ERROR node."""
        if self.type == "ERROR" or self.has_error:
            return True
        return any(c.is_error for c in self.children)

    @property
    def byte_range(self) -> tuple[int, int]:
        """Return (start_index, end_index) as a convenience pair."""
        return (self.start_index, self.end_index)

    @property
    def byte_length(self) -> int:
        """Number of source bytes spanned by this node."""
        return self.end_index - self.start_index

    # -- child access by type --

    def child_by_field_name(self, name: str) -> TsNode | None:
        """Get the first child whose type matches *name*."""
        for c in self.children:
            if c.type == name:
                return c
        return None

    def children_by_type(self, name: str) -> list[TsNode]:
        """Get all direct children whose type matches *name*."""
        return [c for c in self.children if c.type == name]

    def child_by_field(self, _field: str) -> TsNode | None:
        """Stub for tree-sitter's named-field access (not available on TsNode)."""
        return None

    # -- walk / iteration --

    def walk(self) -> Iterator[TsNode]:
        """Depth-first pre-order traversal over this node and all descendants."""
        stack: list[TsNode] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.children))

    def descendants_of_type(self, type_name: str) -> Iterator[TsNode]:
        """Yield all descendant nodes whose type matches *type_name*."""
        for node in self.walk():
            if node.type == type_name:
                yield node

    def first_descendant_of_type(self, type_name: str) -> TsNode | None:
        """Return the first descendant (in pre-order) with the given type, or None."""
        for node in self.walk():
            if node.type == type_name:
                return node
        return None

    def has_descendant_of_type(self, type_name: str) -> bool:
        """Return True if any descendant has the given type."""
        return self.first_descendant_of_type(type_name) is not None

    def named_children(self) -> list[TsNode]:
        """Return children that are named (not punctuation / operators)."""
        return [c for c in self.children if c.is_named]

    # -- debug --

    def sexp(self, max_depth: int = 20, _depth: int = 0) -> str:
        """Return an S-expression representation (like tree-sitter's ``.sexp()``)."""
        if _depth >= max_depth:
            return f"({self.type} …)"
        if not self.children:
            text_repr = json.dumps(self.text)
            return f"({self.type} {text_repr})"
        inner = " ".join(c.sexp(max_depth, _depth + 1) for c in self.children)
        return f"({self.type} {inner})"


# ---------------------------------------------------------------------------
# ParserModule protocol
# ---------------------------------------------------------------------------

class ParserModule(Protocol):
    parse: Callable[[str, float | None], TsNode | None]


# ---------------------------------------------------------------------------
# Module-level parser singleton
# ---------------------------------------------------------------------------

_MODULE: ParserModule | None = None
_parser_instance: BashParser | None = None


def _default_parse(_source: str, _timeout_ms: float | None = None) -> TsNode | None:
    """Placeholder until a native parser is wired."""
    return None


async def ensure_parser_initialized() -> None:
    """No-op compatibility shim (TS pure parser needs no init)."""
    return None


def get_parser_module() -> ParserModule | None:
    global _MODULE
    if _MODULE is None:

        class _Mod:
            parse = staticmethod(_default_parse)

        _MODULE = _Mod()  # type: ignore[assignment]
    return _MODULE


def set_parser_module(module: ParserModule | None) -> None:
    """Test hook to inject a real parser implementation."""
    global _MODULE
    _MODULE = module


async def parse_source(source: str, timeout_ms: float | None = None) -> TsNode | None:
    """Parse *source* into a :class:`TsNode` tree or ``None`` if unavailable."""
    mod = get_parser_module()
    if mod is None:
        return None
    if timeout_ms is None:
        timeout_ms = _PARSE_TIMEOUT_MS
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: mod.parse(source, timeout_ms))


async def parse_command_raw(cmd: str) -> TsNode | None:
    """Entry point used by security analysis (port of parser.parseCommandRaw)."""
    return await parse_source(cmd)


# ============================================================================
# BashParser — tree-sitter integration
# ============================================================================

# Cached capabilities so we avoid re-checking on every call.
_treesitter_available: bool | None = None
_treesitter_bash_lang: Any = None


def _check_tree_sitter() -> bool:
    """Return True if tree-sitter + tree-sitter-bash are importable."""
    global _treesitter_available
    if _treesitter_available is not None:
        return _treesitter_available
    try:
        import tree_sitter  # noqa: F401
        _treesitter_available = True
    except ImportError:
        _treesitter_available = False
    return _treesitter_available


def _load_bash_language() -> Any:
    """Load the tree-sitter-bash grammar and return a Language object.

    Returns None when the grammar cannot be loaded.
    """
    global _treesitter_bash_lang
    if _treesitter_bash_lang is not None:
        return _treesitter_bash_lang
    if not _check_tree_sitter():
        return None

    try:
        import tree_sitter

        # tree-sitter-bash may be bundled as a separate package or as part of
        # tree-sitter-languages.
        lang = None
        errors: list[str] = []

        # Strategy 1: tree_sitter_bash.language()  (tree-sitter-bash >= 0.19)
        try:
            import tree_sitter_bash
            lang = tree_sitter_bash.language()
        except ImportError:
            errors.append("tree_sitter_bash not installed")
        except AttributeError:
            errors.append("tree_sitter_bash has no language()")
        except Exception as exc:
            errors.append(f"tree_sitter_bash.language() error: {exc}")

        # Strategy 2: tree_sitter.Language(lib_path, "bash") where lib_path is a
        # compiled .so/.dylib.
        if lang is None:
            import os
            import sys

            candidates = [
                # tree-sitter-languages pip package
                "tree_sitter_languages",
                # Direct shared library paths (platform-specific)
                f"{sys.prefix}/lib/libtree-sitter-bash.so",
                f"{sys.prefix}/lib/libtree-sitter-bash.dylib",
                "/usr/local/lib/libtree-sitter-bash.so",
                "/usr/local/lib/libtree-sitter-bash.dylib",
                # bundled with tree-sitter-languages
                os.path.join(
                    os.path.dirname(__file__), "..", "..", "grammars", "bash.so"
                ),
            ]
            for candidate in candidates:
                try:
                    if candidate == "tree_sitter_languages":
                        import tree_sitter_languages
                        lang = tree_sitter_languages.get_language("bash")
                        break
                except (ImportError, Exception):
                    continue
                try:
                    if os.path.exists(candidate):
                        lang_obj = tree_sitter.Language(candidate, "bash")
                        lang = lang_obj
                        break
                except Exception:
                    continue

        if lang is not None:
            _treesitter_bash_lang = lang
            return lang

        if errors:
            import logging
            logging.getLogger(__name__).debug(
                "tree-sitter-bash language not loaded: %s", "; ".join(errors)
            )
        return None

    except Exception:
        return None


def is_bash_available() -> bool:
    """Return True when tree-sitter bash parsing is functional."""
    return _check_tree_sitter() and _load_bash_language() is not None


# ---------------------------------------------------------------------------
# native TsNode tree builder
# ---------------------------------------------------------------------------

def _from_tree_sitter_node(ts_node: Any, source_bytes: bytes) -> TsNode:
    """Convert a tree-sitter ``SyntaxNode`` into a :class:`TsNode`."""
    text = (
        source_bytes[ts_node.start_byte : ts_node.end_byte].decode(
            "utf-8", errors="replace"
        )
    )
    children = [
        _from_tree_sitter_node(c, source_bytes)
        for c in ts_node.children
    ]
    return TsNode(
        type=ts_node.type,
        text=text,
        start_index=ts_node.start_byte,
        end_index=ts_node.end_byte,
        children=children,
        is_named=ts_node.is_named if hasattr(ts_node, "is_named") else True,
        has_error=ts_node.has_error if hasattr(ts_node, "has_error") else False,
    )


# ---------------------------------------------------------------------------
# Regex fallback parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
    |(?P<comment>\#[^\n]*)
    |(?P<heredoc_start><<[-]?['"]?[a-zA-Z_][a-zA-Z0-9_!.\-]*['"]?)
    |(?P<redirect>
        <<<|<<-?|>>\|>?|  # <<< <<  <<-  >>|>  >>
        [12]?>>?|          # 2>>  2>  1>>  1>  >>
        &>>?|              # &>>  &>
        >&\-?|             # >&  >&-
        <&\-?|             # <&  <&-
        >\||               # >|
        <>                 # <>
    )
    |(?P<pipe_and>\|&)
    |(?P<pipe>\|)
    |(?P<and_or>&&|\|\|)
    |(?P<double_semi>;;&?|;;&)
    |(?P<bg_semi>[;&])
    |(?P<subshell_open>\()
    |(?P<subshell_close>\))
    |(?P<brace_open>\{)
    |(?P<brace_close>\})
    |(?P<cmd_sub_open>\$\()
    |(?P<arith_open>\$\(\()
    |(?P<proc_sub_open>[<>]\()
    |(?P<dollar>\$)
    |(?P<expansion>\$\{[^}]*\})
    |(?P<simple_expansion>\$[A-Za-z_][A-Za-z0-9_]*|\$[?@*#\-!$_0-9])
    |(?P<backtick>`(?:[^`\\]|\\.)*`)
    |(?P<single_quoted>'(?:[^'\\]|\\.)*')
    |(?P<double_quoted>"(?:[^"\\]|\\.)*")
    |(?P<ansi_c_string>\$'(?:[^'\\]|\\.)*')
    |(?P<number>-?\d+)
    |(?P<test_open>\[\[)
    |(?P<test_close>\]\])
    |(?P<word>[^\s;&|<>()\\'\"$`\n]+)
    """,
    re.VERBOSE | re.DOTALL,
)


def _regex_parse(source: str) -> TsNode:
    """Build a TsNode tree via best-effort regex tokenisation.

    This is a *structural* fallback — it does not produce a real CST, but the
    resulting tree is compatible with the rest of the analysis pipeline.

    Named vs anonymous nodes follow tree-sitter conventions: punctuation and
    operator tokens are anonymous, structural and content tokens are named.
    """
    # Token types considered anonymous (punctuation/operators in tree-sitter).
    _ANON_TYPES: frozenset[str] = frozenset({
        "pipe", "pipe_and", "and_or", "bg_semi", "double_semi",
        "subshell_open", "subshell_close",
        "brace_open", "brace_close",
        "redirect", "space", "dollar",
        "test_open", "test_close",
        "cmd_sub_open", "arith_open", "proc_sub_open",
    })

    children: list[TsNode] = []
    for m in _TOKEN_RE.finditer(source):
        start = m.start()
        end = m.end()
        token_text = source[start:end]
        node_type = m.lastgroup or "word"
        is_named = node_type not in _ANON_TYPES

        # Mark ERROR for backtick content when we can't fully parse it.
        has_error = False
        if node_type == "backtick" and not token_text.endswith("`"):
            has_error = True

        children.append(TsNode(
            type=node_type,
            text=token_text,
            start_index=start,
            end_index=end,
            is_named=is_named,
            has_error=has_error,
        ))

    # If source is empty or produced no tokens, return a minimal valid tree.
    if not children and source:
        children.append(TsNode(
            type="word",
            text=source,
            start_index=0,
            end_index=len(source),
        ))

    return TsNode(
        type="program",
        text=source,
        start_index=0,
        end_index=len(source),
        children=children,
    )


# ---------------------------------------------------------------------------
# BashParser class
# ---------------------------------------------------------------------------

class BashParser:
    """Encapsulates tree-sitter bash parsing.

    Singleton lifecycle managed by :func:`get_bash_parser` / :func:`reset_bash_parser`.
    """

    __slots__ = ("_language", "_parser", "_lock")

    def __init__(self) -> None:
        self._language: Any = None
        self._parser: Any = None
        self._lock = threading.Lock()

    # -- lazy init --

    def _ensure_parser(self) -> Any | None:
        """Return the tree-sitter Parser, or None if unavailable."""
        if self._parser is not None:
            return self._parser

        with self._lock:
            if self._parser is not None:
                return self._parser

            lang = _load_bash_language()
            if lang is None:
                return None

            try:
                import tree_sitter
                parser = tree_sitter.Parser()
                parser.set_language(lang)
                self._language = lang
                self._parser = parser
                return parser
            except Exception:
                return None

    @property
    def language(self) -> Any | None:
        """The tree-sitter Language object, or None."""
        self._ensure_parser()
        return self._language

    @property
    def available(self) -> bool:
        """True when tree-sitter bash parsing is available."""
        return self._ensure_parser() is not None

    # -- parse --

    def parse(
        self,
        source: str,
        timeout_ms: float | None = None,
        *,
        use_timeout: bool = True,
    ) -> TsNode | _ParseAbortedType | None:
        """Parse *source* with tree-sitter-bash.

        Returns:
            :class:`TsNode` tree on success,
            :obj:`PARSE_ABORTED` on timeout,
            ``None`` when tree-sitter is unavailable.
        """
        parser = self._ensure_parser()
        if parser is None:
            return None

        if not source:
            return _regex_parse(source)

        if len(source.encode("utf-8")) > _MAX_SOURCE_LENGTH:
            return PARSE_ABORTED

        source_bytes = source.encode("utf-8")
        if timeout_ms is None:
            timeout_ms = _PARSE_TIMEOUT_MS

        result_container: dict[str, Any] = {"result": PARSE_ABORTED}

        def _do_parse() -> None:
            tree = parser.parse(source_bytes)  # type: ignore[union-attr]
            tree_root = tree.root_node if hasattr(tree, "root_node") else tree
            result_container["result"] = _from_tree_sitter_node(
                tree_root, source_bytes
            )

        if not use_timeout or timeout_ms <= 0:
            _do_parse()
            return result_container["result"]

        thread = threading.Thread(target=_do_parse, daemon=True)
        thread.start()
        thread.join(timeout=timeout_ms / 1000.0)
        if thread.is_alive():
            return PARSE_ABORTED
        return result_container["result"]

    # -- parse with fallback --

    def parse_with_fallback(self, source: str, timeout_ms: float | None = None) -> TsNode:
        """Always return a :class:`TsNode` — delegates to regex fallback when needed."""
        result = self.parse(source, timeout_ms)
        if result is PARSE_ABORTED or result is None:
            return _regex_parse(source)
        return result

    # -- query --

    def query(
        self,
        node: TsNode,
        pattern: str,
        source: str | None = None,
    ) -> list[dict[str, TsNode]]:
        """Execute a tree-sitter query and return a list of capture dicts.

        Each dict maps capture names (prefixed with ``@`` in the pattern) to
        :class:`TsNode` instances.
        """
        parser = self._ensure_parser()
        lang = self._language
        if parser is None or lang is None:
            return []

        try:
            import tree_sitter
            query_obj: Any
            try:
                query_obj = tree_sitter.Query(lang, pattern)
            except TypeError:
                query_obj = self._language.query(pattern)
        except Exception:
            return []

        source_bytes = (source or node.text).encode("utf-8")

        # Re-parse to get a real tree-sitter Tree for the query engine.
        try:
            tree = parser.parse(source_bytes)  # type: ignore[union-attr]
            root_node = tree.root_node if hasattr(tree, "root_node") else tree
        except Exception:
            return []

        results: list[dict[str, TsNode]] = []
        try:
            captures = query_obj.captures(root_node)
        except Exception:
            # Fallback: older tree-sitter API uses ``matches``
            try:
                captures = query_obj.matches(root_node)
                # Convert matches to capture dicts
                for match in captures:
                    cap_dict: dict[str, TsNode] = {}
                    for cap in match.captures:
                        name = cap.name if hasattr(cap, "name") else cap[0]
                        ts_node_raw = cap.node if hasattr(cap, "node") else cap[1]
                        cap_dict[name] = _from_tree_sitter_node(
                            ts_node_raw, source_bytes
                        )
                    results.append(cap_dict)
                return results
            except Exception:
                return []

        for cap_name, cap_node in captures.items() if isinstance(captures, dict) else []:
            # If captures is a dict {name: list[nodes]}
            pass

        # Handle the common tree-sitter Python API shape
        if isinstance(captures, dict):
            # {name: [node, ...]}
            items = list(captures.items())
            if items:
                # zip them into per-match dicts
                names = []
                node_lists = []
                for name, nodes in items:
                    names.append(name)
                    node_lists.append(nodes)
                if node_lists:
                    for i in range(len(node_lists[0])):
                        cap_dict: dict[str, TsNode] = {}
                        for j, name in enumerate(names):
                            if i < len(node_lists[j]):
                                cap_dict[name] = _from_tree_sitter_node(
                                    node_lists[j][i], source_bytes
                                )
                        results.append(cap_dict)
        elif isinstance(captures, list):
            # list of (name, node) tuples
            current: dict[str, TsNode] = {}
            for item in captures:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    name, ts_node_raw = item
                    current[name] = _from_tree_sitter_node(
                        ts_node_raw, source_bytes
                    )
            if current:
                results.append(current)

        return results


# -- global parser instance --

def get_bash_parser() -> BashParser:
    """Return the module-level :class:`BashParser` singleton."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = BashParser()
    return _parser_instance


def reset_bash_parser() -> None:
    """Discard the cached parser (e.g. for testing)."""
    global _parser_instance, _treesitter_available, _treesitter_bash_lang
    _parser_instance = None
    _treesitter_available = None
    _treesitter_bash_lang = None


# ============================================================================
# Query helpers (module-level convenience)
# ============================================================================

# Pre-built query patterns for common bash constructs.

_BASH_QUERY_COMMANDS = """
(command) @command
"""

_BASH_QUERY_VARIABLE_ASSIGNMENTS = """
(variable_assignment) @var_assign
"""

_BASH_QUERY_FUNCTION_DEFS = """
(function_definition) @func_def
"""

_BASH_QUERY_REDIRECTS = """
(file_redirect) @redirect
(heredoc_redirect) @heredoc
(herestring_redirect) @herestring
"""

_BASH_QUERY_EXPANSIONS = """
(command_substitution) @cmd_sub
(process_substitution) @proc_sub
(expansion) @expansion
(simple_expansion) @simple_expansion
"""

_BASH_QUERY_STRINGS = """
(string) @string
(raw_string) @raw_string
(ansi_c_string) @ansi_c
(translated_string) @translated
"""

_BASH_QUERY_COMMENTS = """
(comment) @comment
"""


def query_captures(
    node: TsNode,
    pattern: str,
    source: str | None = None,
) -> list[dict[str, TsNode]]:
    """Execute a tree-sitter query against *node*, returning capture dicts.

    Falls back gracefully when tree-sitter is unavailable.
    """
    parser = get_bash_parser()
    if not parser.available:
        return _regex_captures(node, pattern, source)
    return parser.query(node, pattern, source)


def query_matches(
    node: TsNode,
    pattern: str,
    source: str | None = None,
) -> list[list[dict[str, TsNode]]]:
    """Execute a query and return each match as a list of capture dicts.

    When tree-sitter is available, each match corresponds to one pattern
    match in the AST.  In regex-fallback mode, results are best-effort.

    Returns a list-of-lists: each outer element is one match, each inner
    element is a capture-name→node dict.
    """
    parser = get_bash_parser()
    if not parser.available:
        caps = _regex_captures(node, pattern, source)
        return [[caps[0]]] if caps else []

    if not parser._ensure_parser():
        return []

    try:
        import tree_sitter
        lang = parser._language
        if lang is None:
            return []

        src = (source or node.text).encode("utf-8")
        tree = parser._parser.parse(src)  # type: ignore[union-attr]
        root_node = tree.root_node if hasattr(tree, "root_node") else tree

        query_obj: Any
        try:
            query_obj = tree_sitter.Query(lang, pattern)
        except TypeError:
            query_obj = lang.query(pattern)

        try:
            raw_matches = query_obj.matches(root_node)
        except Exception:
            return []

        results: list[list[dict[str, TsNode]]] = []
        for match in raw_matches:
            match_caps: list[dict[str, TsNode]] = []
            # .matches() returns match objects with .captures iterable
            if hasattr(match, "captures"):
                cap_dict: dict[str, TsNode] = {}
                for cap in match.captures:
                    name = cap.name if hasattr(cap, "name") else str(cap[0])
                    ts_raw = cap.node if hasattr(cap, "node") else cap[1]
                    cap_dict[name] = _from_tree_sitter_node(ts_raw, src)
                if cap_dict:
                    match_caps.append(cap_dict)
            elif isinstance(match, (list, tuple)):
                cap_dict: dict[str, TsNode] = {}
                for item in match:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        name, ts_raw = item
                        cap_dict[str(name)] = _from_tree_sitter_node(ts_raw, src)
                if cap_dict:
                    match_caps.append(cap_dict)
            if match_caps:
                results.append(match_caps)
        return results

    except Exception:
        return []


def _collect_query_captures_from_node(
    node: TsNode,
    capture_names: set[str],
    *,
    _depth: int = 0,
    _max_depth: int = 50,
) -> dict[str, list[TsNode]]:
    """Walk *node* and bin children by capture name for query fallback."""
    result: dict[str, list[TsNode]] = {}
    if _depth >= _max_depth:
        return result
    for child in node.children:
        if child.type in capture_names:
            result.setdefault(child.type, []).append(child)
        sub = _collect_query_captures_from_node(
            child, capture_names, _depth=_depth + 1, _max_depth=_max_depth
        )
        for k, v in sub.items():
            result.setdefault(k, []).extend(v)
    return result


def _regex_captures(
    node: TsNode,
    pattern: str | None = None,
    _source: str | None = None,
) -> list[dict[str, TsNode]]:
    """Crude regex-based capture extractor for when tree-sitter is absent.

    Parses the ``@capture_name`` annotations from *pattern* and maps them to
    the best-matching node types found in the regex token tree.
    """
    # Extract @capture-name annotations from the pattern.
    captured_names: list[str] = []
    if pattern:
        for m in re.finditer(r"@([a-zA-Z_][a-zA-Z0-9_]*)", pattern):
            name = m.group(1)
            if name not in captured_names:
                captured_names.append(name)

    # If no explicit captures, fall back to gathering all interesting node types.
    if not captured_names:
        captured_names = [
            "command", "var_assign", "func_def", "redirect", "heredoc",
            "herestring", "cmd_sub", "proc_sub", "expansion", "simple_expansion",
            "string", "raw_string", "ansi_c", "translated", "comment",
        ]

    # Map capture names to likely node types from the regex tokenizer.
    _CAPTURE_TO_TYPE: dict[str, frozenset[str]] = {
        "command": frozenset({"word"}),
        "var_assign": frozenset({"expansion", "simple_expansion"}),
        "func_def": frozenset({"word"}),
        "redirect": frozenset({"redirect"}),
        "heredoc": frozenset({"heredoc_start"}),
        "herestring": frozenset({"redirect"}),
        "cmd_sub": frozenset({"cmd_sub_open", "backtick"}),
        "proc_sub": frozenset({"proc_sub_open"}),
        "expansion": frozenset({"expansion", "simple_expansion"}),
        "simple_expansion": frozenset({"simple_expansion"}),
        "string": frozenset({"double_quoted"}),
        "raw_string": frozenset({"single_quoted"}),
        "ansi_c": frozenset({"ansi_c_string"}),
        "translated": frozenset({"double_quoted"}),
        "comment": frozenset({"comment"}),
    }

    result: dict[str, TsNode] = {}
    captured: set[str] = set()

    for child in node.walk():
        for cap_name in captured_names:
            if cap_name in captured:
                continue
            target_types = _CAPTURE_TO_TYPE.get(cap_name)
            if target_types and child.type in target_types:
                result[cap_name] = child
                captured.add(cap_name)
                break

    if result:
        return [result]
    return []


# ============================================================================
# Structured result types
# ============================================================================

@dataclass
class CommandInfo:
    """Extracted info about a single command invocation."""

    name: str
    """Executable name (e.g. ``"git"``)."""
    arguments: list[str] = field(default_factory=list)
    """Positional arguments (excluding the executable)."""
    env_vars: dict[str, str] = field(default_factory=dict)
    """Environment variable assignments preceding the command."""
    redirects: list[RedirectInfo] = field(default_factory=list)
    """I/O redirections attached to this command."""
    is_builtin: bool = False
    """True when the command is a shell builtin."""
    is_keyword: bool = False
    """True when the command is a shell keyword (if, for, …)."""
    source_byte_range: tuple[int, int] = (0, 0)
    """Byte offsets of this command in the original source."""

    @property
    def argv(self) -> list[str]:
        """Full ``[name, *arguments]`` as a list."""
        return [self.name] + self.arguments

    @property
    def command_string(self) -> str:
        """Reconstructed command line."""
        return " ".join(self.argv)


@dataclass
class RedirectInfo:
    """Details about a single I/O redirection."""

    operator: str
    """Redirection operator (``>``, ``>>``, ``<``, ``<<``, ``>&``, ``2>``, etc.)."""
    target: str
    """File descriptor, filename, or heredoc delimiter."""
    fd: int | None = None
    """File descriptor number (None if not explicit)."""
    source_byte_range: tuple[int, int] = (0, 0)
    """Byte offsets in the original source."""


@dataclass
class ExpansionInfo:
    """Summary of expansion constructs found in a command."""

    has_command_substitution: bool = False
    has_process_substitution: bool = False
    has_parameter_expansion: bool = False
    has_arithmetic_expansion: bool = False
    has_brace_expansion: bool = False
    has_tilde_expansion: bool = False
    has_backtick: bool = False
    variable_names: list[str] = field(default_factory=list)


@dataclass
class BashParseResult:
    """Result of parsing a bash command with :func:`parse_bash_command`."""

    success: bool = True
    """True when parsing completed without errors."""
    error: str | None = None
    """Error message when ``success`` is False (syntax errors, timeouts)."""
    root: TsNode | None = None
    """The full parse tree."""
    source: str = ""
    """The original command string."""
    commands: list[CommandInfo] = field(default_factory=list)
    """Top-level commands in the source."""
    redirects: list[RedirectInfo] = field(default_factory=list)
    """All I/O redirections found."""
    expansions: ExpansionInfo = field(default_factory=ExpansionInfo)
    """Summary of expansion constructs."""
    has_pipes: bool = False
    """True when the command contains a pipeline."""
    has_logical_ops: bool = False
    """True when ``&&`` or ``||`` are present."""
    has_background: bool = False
    """True when a ``&`` background operator is present."""
    has_subshell: bool = False
    """True when the command runs in a subshell ``( … )``."""
    has_compound_command: bool = False
    """True for ``if/for/while/case`` compound commands."""
    used_fallback: bool = False
    """True when tree-sitter was unavailable and the regex fallback was used."""


# ============================================================================
# Bash command parsing
# ============================================================================

# Regex patterns used by the fallback and by structural analysis.

_COMMAND_NAME_RE = re.compile(
    r"^\s*(?:command\s+|builtin\s+|exec\s+)?"
    r"([a-zA-Z_][a-zA-Z0-9_.\-]*)"
)

_ENV_VAR_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)=("  # name=
    r"(?:[^\"'\s\\]|\\.|"  # unquoted (maybe escaped)
    r"'[^']*'|"             # single-quoted
    r'"[^"]*"|'             # double-quoted
    r"\$\{[^}]*\}|"         # ${...}
    r"\$[A-Za-z_][A-Za-z0-9_]*"  # $VAR
    r")+)\s+"
)

_REDIRECT_RE = re.compile(
    r"(?P<fd>\d+)?\s*"
    r"(?P<op><<<|<<-?|>>\|>?|[12]?>>?|&>>?|>&\-?|<&\-?|>\||<>|<|>)\s*"
    r"(?P<target>\S+)"
)


def _extract_env_vars(text: str) -> tuple[dict[str, str], str]:
    """Strip leading env-var assignments and return (vars, remaining)."""
    env_vars: dict[str, str] = {}
    remaining = text
    while True:
        m = _ENV_VAR_RE.match(remaining)
        if not m:
            break
        env_vars[m.group(1)] = m.group(2)
        remaining = remaining[m.end():]
    return env_vars, remaining


def _extract_redirects_fallback(args: list[str], pos: int = 0) -> tuple[list[RedirectInfo], list[str], int]:
    """Pull redirections from a token list; returns (redirects, clean_args, byte_end)."""
    redirects: list[RedirectInfo] = []
    clean_args: list[str] = []
    i = 0
    while i < len(args):
        m = _REDIRECT_RE.match("".join(args[i:]))
        if m:
            op = m.group("op")
            target = m.group("target")
            fd = int(m.group("fd")) if m.group("fd") else None
            redirects.append(RedirectInfo(
                operator=op,
                target=target,
                fd=fd,
                source_byte_range=(pos, pos + len(op) + len(target)),
            ))
            i += 1
            continue
        clean_args.append(args[i])
        i += 1
    return redirects, clean_args, pos


def _analyze_expansions(source: str) -> ExpansionInfo:
    """Scan *source* for shell expansion patterns.

    Detects parameter expansions (``${VAR}``, ``$VAR``), command substitution
    (``$(cmd)``, backticks), process substitution (``<(cmd)``, ``>(cmd)``),
    arithmetic expansion (``$((expr))``), brace expansion (``{1..5}``,
    ``{a,b,c}``), and tilde expansion.

    All checks respect quoting: expansions inside single-quoted strings are
    not counted.
    """
    info = ExpansionInfo()

    # Determine whether a given position is inside a single-quoted region.
    # Build a quick bitmap: positions where we're inside single quotes.
    in_single_at: list[int] = []
    in_single = False
    in_double = False
    for i, ch in enumerate(source):
        if ch == "\\" and i + 1 < len(source):
            in_single_at.append(int(in_single))
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        in_single_at.append(int(in_single))

    def _in_single(pos: int) -> bool:
        if pos < 0 or pos >= len(in_single_at):
            return False
        return bool(in_single_at[pos])

    # -- parameter expansion: ${VAR...} --
    indexed = False
    for m in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)([:}/\[#%\-+=?])?", source):
        if not _in_single(m.start()):
            indexed = True
            info.has_parameter_expansion = True
            name = m.group(1)
            if name and name not in info.variable_names:
                info.variable_names.append(name)

    # -- simple expansion: $VAR, $?, $!, etc. (not followed by ( or {) --
    if not indexed:
        for m in re.finditer(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|([?@*#\-!$_0-9]))", source):
            if not _in_single(m.start()):
                info.has_parameter_expansion = True
                name = m.group(1) or m.group(2)
                if name and len(name) > 1 and name not in info.variable_names:
                    info.variable_names.append(name)

    # -- command substitution: $(cmd) --
    if "$(" in source:
        for m in re.finditer(r"\$\(", source):
            if not _in_single(m.start()):
                info.has_command_substitution = True
                break

    # -- process substitution: <(cmd) or >(cmd) --
    if re.search(r"[<>]\(", source):
        for m in re.finditer(r"[<>]\(", source):
            if not _in_single(m.start()):
                info.has_process_substitution = True
                break

    # -- arithmetic expansion: $((expr)) --
    if "$((" in source:
        for m in re.finditer(r"\$\(\(?", source):
            if not _in_single(m.start()):
                info.has_arithmetic_expansion = True
                break

    # -- brace expansion: {1..5} or {a,b,c} --
    _brace_seq = re.compile(r"\{[a-zA-Z0-9_,.\-]+\.\.[a-zA-Z0-9_,.\-]+\}")
    _brace_list = re.compile(r"\{[a-zA-Z0-9_]+,[a-zA-Z0-9_,]+\}")
    for m in re.finditer(_brace_seq, source):
        if not _in_single(m.start()):
            info.has_brace_expansion = True
            break
    if not info.has_brace_expansion:
        for m in re.finditer(_brace_list, source):
            if not _in_single(m.start()):
                info.has_brace_expansion = True
                break

    # -- tilde expansion: ~ or ~user at word boundaries --
    if "~" in source:
        for m in re.finditer(r"(?:^|[\s=;|&<>(])~([A-Za-z_][A-Za-z0-9_\-]*)?", source):
            if not _in_single(m.start()):
                info.has_tilde_expansion = True
                break

    # -- backtick command substitution: `cmd` --
    if "`" in source:
        for i, ch in enumerate(source):
            if ch == "`" and not _in_single(i):
                info.has_backtick = True
                break

    # Deduplicate variable names while preserving order.
    seen: set[str] = set()
    unique_names: list[str] = []
    for vn in info.variable_names:
        if vn not in seen:
            seen.add(vn)
            unique_names.append(vn)
    info.variable_names = unique_names

    return info


def _extract_commands_from_node(root: TsNode, source: str) -> list[CommandInfo]:
    """Walk the AST and extract :class:`CommandInfo` for each top-level command.

    Handles: command, declaration_command, unset_command, negated_command,
    test_command, subshell, variable_assignment, function_definition, and
    all control-flow constructs (if/for/while/until/case/select).
    """
    commands: list[CommandInfo] = []

    def _visit(node: TsNode) -> None:
        if node.type == "command":
            cmd_info = _command_info_from_command_node(node, source)
            if cmd_info is not None:
                commands.append(cmd_info)
            return
        if node.type in {
            "variable_assignment",
            "declaration_command",
            "negated_command",
            "unset_command",
        }:
            cmd_info = _command_info_from_command_node(node, source)
            if cmd_info is not None:
                commands.append(cmd_info)
            return
        if node.type == "test_command":
            # Extract the condition as a pseudo-command.
            name = "test" if any(
                c.type == "[" for c in node.children
            ) else "[["
            commands.append(CommandInfo(
                name=name,
                is_builtin=True,
                source_byte_range=(node.start_index, node.end_index),
            ))
            return
        if node.type == "subshell":
            commands.append(CommandInfo(
                name="(subshell)",
                is_keyword=True,
                source_byte_range=(node.start_index, node.end_index),
            ))
            # Recurse into the subshell body.
            for child in node.children:
                if child.type not in ("(", ")"):
                    _visit(child)
            return
        if node.type == "redirected_statement":
            for child in node.children:
                _visit(child)
            return
        if node.type in {
            "if_statement",
            "while_statement",
            "until_statement",
            "for_statement",
            "case_statement",
            "select_statement",
        }:
            keyword = node.type.replace("_statement", "")
            commands.append(CommandInfo(
                name=keyword,
                is_keyword=True,
                source_byte_range=(node.start_index, node.end_index),
            ))
            return
        if node.type == "c_style_for_statement":
            commands.append(CommandInfo(
                name="for",
                is_keyword=True,
                source_byte_range=(node.start_index, node.end_index),
            ))
            return
        if node.type == "function_definition":
            name = "function"
            # Try to extract the actual function name.
            name_node = node.first_descendant_of_type("word")
            if name_node is not None:
                name = name_node.text.strip()
            commands.append(CommandInfo(
                name=name,
                is_keyword=True,
                source_byte_range=(node.start_index, node.end_index),
            ))
            return
        for child in node.children:
            _visit(child)

    _visit(root)

    # If no commands found via AST, try the fallback extractor.
    if not commands and source.strip():
        commands = _extract_commands_fallback(source)

    return commands


def _command_info_from_command_node(node: TsNode, source: str) -> CommandInfo | None:
    """Extract a single CommandInfo from a ``command`` or similar AST node.

    Walks only direct children (not grandchildren) to avoid collecting the
    word node inside ``command_name`` as a positional argument.  Redirect
    targets and env-var values are handled at the same level.
    """
    # Find the command_name / first word child.
    name_node: TsNode | None = None
    for child in node.children:
        if child.type == "command_name":
            name_node = child
            break
    if name_node is None:
        for child in node.children:
            if child.type == "word" and child.is_named:
                name_node = child
                break
    if name_node is None:
        # Some tree-sitter grammars nest command_name inside the command.
        name_node = node.first_descendant_of_type("command_name")
    if name_node is None:
        word_nodes = list(node.descendants_of_type("word"))
        if word_nodes:
            name_node = word_nodes[0]
    if name_node is None:
        return None

    name = name_node.text.strip()
    if not name:
        return None

    # Walk DIRECT children only — avoids collecting command_name's inner word.
    args: list[str] = []
    redirects: list[RedirectInfo] = []
    env_vars: dict[str, str] = {}

    seen_redirect = False
    for child in node.children:
        # Skip the command_name container itself.
        if child is name_node:
            continue
        # Skip unnamed operator/punctuation tokens.
        if child.type in ("(", ")", "!", "{", "}", "[", "]", "[[", "]]"):
            continue

        if child.type == "variable_assignment":
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\+)?=(.*)", child.text)
            if m:
                env_vars[m.group(1)] = m.group(2)
            continue

        if child.type in ("file_redirect", "heredoc_redirect", "herestring_redirect"):
            seen_redirect = True
            redirects.extend(_extract_redirects_from_ast_redirect(child))
            continue

        # Once a file_redirect has been seen, tree-sitter's prec.left means
        # subsequent words belong to the redirect, not the command.
        if seen_redirect:
            continue

        if child.type in ("word", "string", "raw_string", "ansi_c_string",
                          "translated_string", "concatenation"):
            raw = child.text.strip()
            if raw:
                args.append(raw)
        elif child.type == "command_substitution":
            args.append(child.text)
        elif child.type == "expansion" or child.type == "simple_expansion":
            # Expansions can appear as command arguments.
            args.append(child.text)

    # Also scan the command_name's text to see if it IS an env assignment.
    is_keyword = name in SHELL_KEYWORDS
    is_builtin = name in _DECL_KEYWORDS or name in {
        "command", "builtin", "exec", "source", ".",
        "alias", "unalias", "bg", "fg", "jobs", "wait",
        "cd", "echo", "eval", "exit", "hash", "pwd",
        "read", "set", "shift", "test", "times", "trap",
        "type", "ulimit", "umask", "unset",
    }

    return CommandInfo(
        name=name,
        arguments=args,
        env_vars=env_vars,
        redirects=redirects,
        is_builtin=is_builtin,
        is_keyword=is_keyword,
        source_byte_range=(node.start_index, node.end_index),
    )


# Valid redirect operator tokens as recognised by tree-sitter-bash.
_REDIRECT_OPS: frozenset[str] = frozenset({
    ">", ">>", "<", "<<", "<<<", "<<-",
    ">&", "<&", ">&-", "<&-",
    "&>", "&>>", ">|", "<>",
})


def _extract_redirects_from_ast_redirect(node: TsNode) -> list[RedirectInfo]:
    """Extract RedirectInfo from a file_redirect / heredoc_redirect / herestring AST node."""
    redirects: list[RedirectInfo] = []

    # Find operator node: check named fields first, then scan children.
    op_node: TsNode | None = None
    for child in node.children:
        if child.type in _REDIRECT_OPS:
            op_node = child
            break

    # Find file descriptor child (file_descriptor node type in tree-sitter).
    fd: int | None = None
    fd_node: TsNode | None = None
    for child in node.children:
        if child.type == "file_descriptor":
            fd_node = child
            try:
                fd = int(child.text)
            except ValueError:
                pass
            break

    # Build operator string.
    operator = op_node.text if op_node else ">"

    # Determine target: word, string, heredoc_start, or concatenation.
    target = ""
    for child in node.children:
        if child is op_node or child is fd_node:
            continue
        if child.type in ("word", "string", "raw_string", "ansi_c_string",
                          "translated_string", "concatenation",
                          "simple_expansion", "expansion",
                          "process_substitution", "command_substitution"):
            target = child.text
            break
        if child.type == "heredoc_start":
            target = child.text
            break
        if child.type == "heredoc_body":
            target = child.text
            break

    redirects.append(RedirectInfo(
        operator=operator,
        target=target,
        fd=fd,
        source_byte_range=(node.start_index, node.end_index),
    ))
    return redirects


def _extract_commands_fallback(source: str) -> list[CommandInfo]:
    """Regex-based command extraction when tree-sitter is unavailable.

    Splits on ``;``, ``&&``, ``||``, and ``|`` (pipeline) separators,
    respecting shell quoting so that operators inside strings do not
    trigger a split.
    """
    commands: list[CommandInfo] = []
    if not source or not source.strip():
        return commands

    # Split on compound separators first: ;  &&  ||
    segments: list[str] = [source]
    for sep in ("&&", "||", ";"):
        new_segments: list[str] = []
        for seg in segments:
            parts = _split_outside_quotes(seg, sep)
            new_segments.extend(parts)
        segments = new_segments

    # Further split each segment on | (pipeline) to produce individual commands.
    pipeline_segments: list[str] = []
    for seg in segments:
        parts = _split_outside_quotes(seg, "|")
        pipeline_segments.extend(parts)

    offset = 0
    for seg in pipeline_segments:
        seg = seg.strip()
        if not seg:
            continue
        env_vars, rest = _extract_env_vars(seg)
        m = _COMMAND_NAME_RE.match(rest)
        if m:
            name = m.group(1)
            args_raw = rest[m.end():].strip()
            args = []
            if args_raw:
                try:
                    import shlex
                    args = shlex.split(args_raw)
                except ValueError:
                    args = [a for a in args_raw.split() if a]
            commands.append(CommandInfo(
                name=name,
                arguments=args,
                env_vars=env_vars,
                is_keyword=name in SHELL_KEYWORDS,
                source_byte_range=(offset, offset + len(seg)),
            ))
        offset += len(seg) + 1  # rough estimate for byte range

    return commands


def _split_outside_quotes(text: str, separator: str) -> list[str]:
    """Split *text* on *separator* only when it appears outside quotes."""
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            current.append(ch)
            current.append(text[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if text[i:i + len(separator)] == separator:
                parts.append("".join(current).strip())
                current = []
                i += len(separator)
                continue
        current.append(ch)
        i += 1
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _detect_structural_features(source: str) -> dict[str, bool]:
    """Detect structural features (pipes, logic, subshells, etc.) from source.

    All checks respect quoting: operators inside strings are ignored.
    """
    def _has_outside_quotes(text: str, needle: str) -> bool:
        """True when *needle* appears in *text* outside of quotes."""
        in_single = False
        in_double = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\\" and i + 1 < len(text):
                i += 2
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue
            if not in_single and not in_double:
                if text[i:i + len(needle)] == needle:
                    return True
            i += 1
        return False

    # Detect pipes: single | not preceded or followed by another |.
    has_pipe = False
    if "|" in source:
        # Check for a single | outside quotes (exclude || and |&).
        in_single = False
        in_double = False
        idx = 0
        while idx < len(source):
            ch = source[idx]
            if ch == "\\" and idx + 1 < len(source):
                idx += 2
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double and ch == "|":
                # Check neighbors: not || and not |&
                if idx + 1 < len(source) and source[idx + 1] in ("|", "&"):
                    idx += 1
                    continue
                if idx - 1 >= 0 and source[idx - 1] == "|":
                    idx += 1
                    continue
                has_pipe = True
                break
            idx += 1

    return {
        "has_pipes": has_pipe,
        "has_logical_ops": _has_outside_quotes(source, "&&") or
                           _has_outside_quotes(source, "||"),
        "has_background": (
            source.rstrip().endswith("&")
            and not source.rstrip().endswith("&&")
            and not source.rstrip().endswith(">&")
        ),
        "has_subshell": bool(
            re.search(r"\([^)]*\)", source) and not _has_outside_quotes(source, "$(")
        ),
        "has_compound_command": bool(
            re.search(
                r"\b(?:if|then|elif|else|fi|while|until|for|do|done|case|esac|function|select)\b",
                source,
            )
        ),
    }


# -- public parse entry points --

async def parse_bash_command(
    command: str,
    *,
    timeout_ms: float | None = None,
    use_tree_sitter: bool = True,
) -> BashParseResult:
    """Parse a bash command string and return a structured :class:`BashParseResult`.

    Parameters
    ----------
    command:
        The shell command to parse.
    timeout_ms:
        Maximum time to spend in tree-sitter parsing (default: 50 ms).
    use_tree_sitter:
        When False, skip tree-sitter and use regex fallback only.
    """
    if use_tree_sitter:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: parse_bash_command_sync(command, timeout_ms=timeout_ms)
        )
    return parse_bash_command_sync(command, use_tree_sitter=False, timeout_ms=timeout_ms)


def parse_bash_command_sync(
    command: str,
    *,
    timeout_ms: float | None = None,
    use_tree_sitter: bool = True,
) -> BashParseResult:
    """Synchronous version of :func:`parse_bash_command`."""
    if not command or not command.strip():
        return BashParseResult(
            success=True,
            source=command,
            root=TsNode(type="program", text="", start_index=0, end_index=0),
        )

    parser = get_bash_parser()
    root: TsNode | None = None
    used_fallback = False

    if use_tree_sitter and parser.available:
        parse_outcome = parser.parse(command, timeout_ms)
        if parse_outcome is PARSE_ABORTED:
            return BashParseResult(
                success=False,
                error="Parse timed out",
                source=command,
                used_fallback=False,
            )
        if parse_outcome is not None:
            root = parse_outcome

    if root is None:
        # Validate that the command is structurally plausible before
        # returning a fallback parse.
        if _contains_unclosed_quotes(command):
            return BashParseResult(
                success=False,
                error="Unclosed quote in command",
                source=command,
            )
        root = _regex_parse(command)
        used_fallback = True

    commands = _extract_commands_from_node(root, command)
    redirects = _collect_all_redirects(root)
    expansions = _analyze_expansions(command)
    features = _detect_structural_features(command)

    has_error = root.is_error if root else False

    return BashParseResult(
        success=not has_error,
        error="Syntax error detected by parser" if has_error else None,
        root=root,
        source=command,
        commands=commands,
        redirects=redirects,
        expansions=expansions,
        has_pipes=features["has_pipes"],
        has_logical_ops=features["has_logical_ops"],
        has_background=features["has_background"],
        has_subshell=features["has_subshell"],
        has_compound_command=features["has_compound_command"],
        used_fallback=used_fallback,
    )


def _contains_unclosed_quotes(text: str) -> bool:
    """Return True if *text* contains unclosed single or double quotes."""
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        i += 1
    return in_single or in_double


def _collect_all_redirects(root: TsNode) -> list[RedirectInfo]:
    """Walk the tree and gather every :class:`RedirectInfo`."""
    redirects: list[RedirectInfo] = []
    for node in root.descendants_of_type("file_redirect"):
        redirects.extend(_extract_redirects_from_ast_redirect(node))
    for node in root.descendants_of_type("heredoc_redirect"):
        redirects.extend(_extract_redirects_from_ast_redirect(node))
    for node in root.descendants_of_type("herestring_redirect"):
        redirects.extend(_extract_redirects_from_ast_redirect(node))
    return redirects


# ============================================================================
# Syntax checking
# ============================================================================

def check_bash_syntax(command: str) -> tuple[bool, str | None]:
    """Quick syntax validation without building a full parse tree.

    Returns ``(is_valid, error_message)``.
    """
    if not command or not command.strip():
        return (True, None)

    if _contains_unclosed_quotes(command):
        return (False, "Unclosed quote")

    # Check balanced parentheses/brackets outside quotes.
    if not _balanced_brackets(command):
        return (False, "Unbalanced brackets or parentheses")

    # Try tree-sitter if available.
    parser = get_bash_parser()
    if parser.available:
        result = parser.parse(command, use_timeout=True)
        if result is PARSE_ABORTED:
            return (False, "Parse timed out")
        if result is not None:
            if result.is_error:
                error_nodes = list(result.descendants_of_type("ERROR"))
                if error_nodes:
                    return (False, f"Syntax error near '{error_nodes[0].text[:40]}'")
                return (False, "Syntax error")
            return (True, None)

    return (True, None)


def _balanced_brackets(text: str) -> bool:
    """Check that parentheses, brackets, and braces are balanced, ignoring quoted regions."""
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = set(pairs.values())
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if in_single or in_double:
            i += 1
            continue
        if ch in pairs:
            stack.append(ch)
        elif ch in closing:
            if not stack:
                return False
            opener = stack.pop()
            if pairs[opener] != ch:
                return False
        i += 1
    return len(stack) == 0


# ============================================================================
# Tree traversal utilities
# ============================================================================

def walk_tree(root: TsNode) -> Iterator[TsNode]:
    """Depth-first pre-order iterator over the tree rooted at *root*."""
    yield from root.walk()


def find_nodes_of_type(root: TsNode, type_name: str) -> list[TsNode]:
    """Return all descendant nodes whose type matches *type_name*."""
    return list(root.descendants_of_type(type_name))


def get_child_by_type(node: TsNode, type_name: str) -> TsNode | None:
    """Return the first direct child whose type matches *type_name*."""
    return node.child_by_field_name(type_name)


def get_children_by_type(node: TsNode, type_name: str) -> list[TsNode]:
    """Return all direct children whose type matches *type_name*."""
    return node.children_by_type(type_name)


# ============================================================================
# Serialization
# ============================================================================

def ts_node_to_dict(node: TsNode) -> dict[str, Any]:
    """Convert a :class:`TsNode` tree to a plain dict (for JSON)."""
    return {
        "type": node.type,
        "text": node.text,
        "startIndex": node.start_index,
        "endIndex": node.end_index,
        "isNamed": node.is_named,
        "hasError": node.has_error,
        "children": [ts_node_to_dict(c) for c in node.children],
    }


def ts_node_from_dict(d: dict[str, Any]) -> TsNode:
    """Reconstruct a :class:`TsNode` tree from a dict produced by :func:`ts_node_to_dict`."""
    return TsNode(
        type=d.get("type", ""),
        text=d.get("text", ""),
        start_index=d.get("startIndex", d.get("start_index", 0)),
        end_index=d.get("endIndex", d.get("end_index", 0)),
        is_named=d.get("isNamed", d.get("is_named", True)),
        has_error=d.get("hasError", d.get("has_error", False)),
        children=[ts_node_from_dict(c) for c in d.get("children", [])],
    )


def ts_node_to_json(node: TsNode, *, indent: int | None = 2) -> str:
    """Serialize *node* to a JSON string."""
    return json.dumps(ts_node_to_dict(node), indent=indent, ensure_ascii=False)


def dump_tree(node: TsNode, *, indent: int = 0, _depth: int = 0) -> str:
    """Pretty-print multi-line dump of an AST (for debugging).

    Example output::

        program [0..25]
          command [0..11]
            command_name [0..3] "git"
            word [4..11] "status"
          command [13..25]
            command_name [13..19] "python"
    """
    prefix = "  " * _depth
    lines = [f"{prefix}{node.type} [{node.start_index}..{node.end_index}]"]
    if node.text and node.children:
        text_preview = node.text.replace("\n", "\\n")
        lines[-1] += f' "{text_preview}"' if len(text_preview) < 60 else ""
    for child in node.children:
        lines.append(dump_tree(child, indent=indent, _depth=_depth + 1))
    return "\n".join(lines)


# ============================================================================
# Node type predicates (module-level)
# ============================================================================

# These provide fast type checks without importing hare.utils.bash.ast.
# Re-exported from ast.py when that module is available, but defined here
# for direct use by callers that only depend on bash_parser.

# Pre-built frozensets for quick membership tests.
_EXECUTABLE_TYPES: frozenset[str] = frozenset(
    {"command", "declaration_command", "unset_command", "negated_command"}
)

_CONTROL_FLOW_TYPES: frozenset[str] = frozenset({
    "if_statement", "for_statement", "c_style_for_statement",
    "while_statement", "until_statement", "case_statement",
    "select_statement", "function_definition",
    "elif_clause", "else_clause", "do_group", "case_item",
})

_REDIRECT_TYPES: frozenset[str] = frozenset({
    "file_redirect", "heredoc_redirect", "herestring_redirect",
    "heredoc_body", "heredoc_start",
})

_STRING_TYPES: frozenset[str] = frozenset({
    "word", "string", "raw_string", "ansi_c_string",
    "translated_string", "concatenation",
})

_EXPANSION_TYPES: frozenset[str] = frozenset({
    "expansion", "simple_expansion", "string_expansion",
    "brace_expression", "command_substitution",
    "process_substitution", "arithmetic_expansion",
})

_OPERATOR_TYPES: frozenset[str] = frozenset({
    "&&", "||", "|", "|&", ";", ";;", ";&", ";;&", "&",
    ">", ">>", "<", "<<", ">&", "<&", ">|", "<>",
    "&>", "&>>",
})

_STRUCTURE_TYPES: frozenset[str] = frozenset({
    "program", "list", "pipeline",
})


def is_command_like_node(node_type: str) -> bool:
    """Return True for nodes that carry an executable name."""
    return node_type in _EXECUTABLE_TYPES


def is_control_flow_node(node_type: str) -> bool:
    """Return True for if/for/while/until/case/function nodes."""
    return node_type in _CONTROL_FLOW_TYPES


def is_redirect_node(node_type: str) -> bool:
    """Return True for file-redirect, heredoc, or herestring node types."""
    return node_type in _REDIRECT_TYPES


def is_string_node(node_type: str) -> bool:
    """Return True for word, quoted-string, raw-string, or concatenation."""
    return node_type in _STRING_TYPES


def is_expansion_node(node_type: str) -> bool:
    """Return True for any shell expansion / substitution node type."""
    return node_type in _EXPANSION_TYPES


def is_operator_node(node_type: str) -> bool:
    """Return True for shell operator tokens."""
    return node_type in _OPERATOR_TYPES


def is_comment_node(node_type: str) -> bool:
    """Return True for comment nodes."""
    return node_type == "comment"


def is_error_node(node_type: str) -> bool:
    """Return True for ERROR nodes."""
    return node_type == "ERROR"


def is_list_node(node_type: str) -> bool:
    """Return True for list or pipeline container nodes."""
    return node_type in _STRUCTURE_TYPES


# ============================================================================
# Expanded command extraction helpers
# ============================================================================


# ============================================================================
# Public shell-syntax helpers
# ============================================================================


def split_command_outside_quotes(text: str, separator: str) -> list[str]:
    """Split *text* on *separator* only when it appears outside quotes.

    Public wrapper around :func:`_split_outside_quotes`.
    """
    return _split_outside_quotes(text, separator)


def contains_unclosed_quotes(text: str) -> bool:
    """Return True if *text* has unclosed single or double quotes.

    Public wrapper around :func:`_contains_unclosed_quotes`.
    """
    return _contains_unclosed_quotes(text)


def balanced_brackets(text: str) -> bool:
    """Return True when parentheses, brackets, and braces are balanced.

    Public wrapper around :func:`_balanced_brackets`.
    """
    return _balanced_brackets(text)


def _extract_command_name_from_node(node: TsNode) -> TsNode | None:
    """Find the command_name child or first word in a command-like node."""
    # Direct 'command_name' child.
    name_node = node.first_descendant_of_type("command_name")
    if name_node is not None:
        return name_node
    # First top-level 'word' child.
    for child in node.children:
        if child.type == "word" and child.is_named:
            return child
    # Any descendant 'word'.
    word_nodes = list(node.descendants_of_type("word"))
    if word_nodes:
        # Return the first non-empty word.
        for w in word_nodes:
            if w.text.strip():
                return w
    return None


def _extract_arguments_from_command_node(node: TsNode, name_node: TsNode) -> list[str]:
    """Extract positional arguments from a command node.

    Collects word/string/concatenation children that are NOT the command name
    and NOT redirect targets.
    """
    args: list[str] = []
    seen_redirect = False
    for child in node.children:
        if child is name_node:
            continue
        # Once we hit a file_redirect, remaining words belong to it in
        # tree-sitter grammar (prec.left). Stop collecting args.
        if child.type == "file_redirect":
            seen_redirect = True
            continue
        if seen_redirect:
            continue
        if child.type == "heredoc_redirect" or child.type == "herestring_redirect":
            continue
        if child.type in ("word", "string", "raw_string", "concatenation"):
            text = child.text.strip()
            if text:
                args.append(text)
        elif child.type == "variable_assignment":
            continue
    return args


def _extract_env_vars_from_command_node(node: TsNode) -> dict[str, str]:
    """Extract environment variable assignments from a command node."""
    env_vars: dict[str, str] = {}
    for child in node.children:
        if child.type == "variable_assignment":
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\+)?=(.*)", child.text)
            if m:
                env_vars[m.group(1)] = m.group(2)
    return env_vars
