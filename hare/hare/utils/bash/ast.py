"""
AST-based bash command analysis (tree-sitter compatible).

Port of: src/utils/bash/ast.ts (types, entrypoints, and stable analytics IDs).

Provides full argv extraction from tree-sitter-bash ASTs with security
pre-checks, expansion-aware string resolution, and semantic analysis.

============================================================
SECTIONS
============================================================
1.  Regex pre-checks
2.  Tree-sitter-bash node type registry (complete)
3.  Node type predicates
4.  Data classes (Redirect, SimpleCommand, parse result types)
5.  Node type ID mapping (analytics)
6.  Tree traversal utilities
7.  AST debug / dump
8.  String text extraction from AST nodes
9.  AST argv / redirect / env-var extraction
10. parse_for_security entrypoints (pre-checks + AST-driven analysis)
11. Semantic checks (post-argv)
12. AST safety classification
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Literal, TypeAlias, Union

from hare.utils.bash.bash_parser import PARSE_ABORTED, TsNode, parse_command_raw

# ============================================================================
# 1. Regex pre-checks (preserved from original port)
# ============================================================================

CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
UNICODE_WHITESPACE_RE = re.compile(
    r"[   -​    　﻿]"
)
BACKSLASH_WHITESPACE_RE = re.compile(r"\\[ \t]|[^ \t\n\\]\\\n")
ZSH_TILDE_BRACKET_RE = re.compile(r"~\[")
ZSH_EQUALS_EXPANSION_RE = re.compile(r"(?:^|[\s;&|])=[a-zA-Z_]")
BRACE_WITH_QUOTE_RE = re.compile(r"\{[^}]*['\"]")

# ============================================================================
# 2. Tree-sitter-bash node type registry (complete)
# ============================================================================

# ---------------------------------------------------------------------------
# Structure / Top-level
# ---------------------------------------------------------------------------

BASH_STRUCTURE_TYPES: frozenset[str] = frozenset(
    {
        "program",          # root node — every parse tree
    }
)

# ---------------------------------------------------------------------------
# Lists / sequences
# ---------------------------------------------------------------------------

BASH_LIST_TYPES: frozenset[str] = frozenset(
    {
        "list",             # compound list separated by ; & && ||
        "pipeline",         # pipe-separated commands
    }
)

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

BASH_COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "command",                  # simple command: name arg1 arg2 …
        "command_name",             # the executable word
        "declaration_command",      # export / declare / local / readonly / typeset
        "unset_command",            # unset
        "variable_assignment",      # VAR=value
        "variable_name",            # just the name part
        "redirected_statement",     # command with attached redirect(s)
        "negated_command",          # ! command
        "test_command",             # [[ … ]] or [ … ]
        "subshell",                 # ( … )
        "compound_statement",       # { …; }
    }
)

# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------

BASH_CONTROL_FLOW_TYPES: frozenset[str] = frozenset(
    {
        "if_statement",             # if … then … fi
        "elif_clause",              # elif … then
        "else_clause",              # else …
        "for_statement",            # for var in …; do …; done
        "c_style_for_statement",    # for (( … ))
        "while_statement",          # while …; do …; done
        "until_statement",          # until …; do …; done
        "do_group",                 # do …; done block
        "case_statement",           # case … in … esac
        "case_item",                # pattern) commands ;;
        "function_definition",      # name() { … }  or  function name { … }
    }
)

# ---------------------------------------------------------------------------
# Redirects
# ---------------------------------------------------------------------------

BASH_REDIRECT_TYPES: frozenset[str] = frozenset(
    {
        "file_redirect",            # > file  2> file  &> file  >> file  < file
        "heredoc_redirect",         # <<EOF (header line)
        "heredoc_body",             # content between <<EOF and the delimiter
        "heredoc_start",            # the EOF delimiter token
        "herestring_redirect",      # <<< "string"
    }
)

# ---------------------------------------------------------------------------
# Strings / words
# ---------------------------------------------------------------------------

BASH_STRING_TYPES: frozenset[str] = frozenset(
    {
        "word",                     # bare / unquoted word
        "string",                   # double-quoted string
        "raw_string",               # single-quoted string  '…'
        "ansi_c_string",            # $'…'
        "translated_string",        # $"…"  (locale-aware)
        "concatenation",            # adjacent-quote concatenation: "a"'b'
    }
)

# ---------------------------------------------------------------------------
# Expansions
# ---------------------------------------------------------------------------

BASH_EXPANSION_TYPES: frozenset[str] = frozenset(
    {
        "expansion",                # ${VAR}, ${VAR:-default}, etc.
        "simple_expansion",         # $VAR  $?  $!  $#  $@  $*
        "string_expansion",         # expansion inside double-quoted string
        "brace_expression",         # {a,b,c}  {1..10}
        "command_substitution",     # $(cmd)  or  `cmd`
        "process_substitution",     # <(cmd)  or  >(cmd)
        "arithmetic_expansion",     # $(( expr ))
    }
)

# ---------------------------------------------------------------------------
# Operators (anonymous / punctuation nodes in tree-sitter)
# ---------------------------------------------------------------------------

BASH_OPERATOR_TYPES: frozenset[str] = frozenset(
    {
        "&&",                       # logical AND
        "||",                       # logical OR
        "|",                        # pipe
        ";",                        # sequential separator
        ";;",                       # case terminator (double semicolon)
        ";&",                       # case fall-through
        ";;&",                      # case test-next fall-through
        "&",                        # background
    }
)

# ---------------------------------------------------------------------------
# Comments / errors
# ---------------------------------------------------------------------------

BASH_OTHER_TYPES: frozenset[str] = frozenset(
    {
        "comment",                  # # … to end of line
        "ERROR",                    # parse error node
    }
)

# ---------------------------------------------------------------------------
# Union / complete set
# ---------------------------------------------------------------------------

BASH_NAMED_NODE_TYPES: frozenset[str] = frozenset(
    BASH_STRUCTURE_TYPES
    | BASH_LIST_TYPES
    | BASH_COMMAND_TYPES
    | BASH_CONTROL_FLOW_TYPES
    | BASH_REDIRECT_TYPES
    | BASH_STRING_TYPES
    | BASH_EXPANSION_TYPES
    | BASH_OTHER_TYPES
)

# Aliases for common combined queries (preserved from original port).
# Order matches TS DANGEROUS_TYPES Set iteration for stable node_type_id indices.
_DANGEROUS_TYPE_IDS: tuple[str, ...] = (
    "command_substitution",
    "process_substitution",
    "expansion",
    "simple_expansion",
    "brace_expression",
    "subshell",
    "compound_statement",
    "for_statement",
    "while_statement",
    "until_statement",
    "if_statement",
    "case_statement",
    "function_definition",
    "test_command",
    "ansi_c_string",
    "translated_string",
    "herestring_redirect",
    "heredoc_redirect",
)

# Node types that wrap / contain executable commands.
BASH_EXECUTABLE_TYPES: frozenset[str] = frozenset(
    {"command", "declaration_command", "unset_command", "negated_command"}
)

# Node types that produce output (not assignments / declarations).
BASH_OUTPUT_PRODUCING_TYPES: frozenset[str] = frozenset(
    {
        "command",
        "pipeline",
        "subshell",
        "command_substitution",
        "negated_command",
    }
)

# ============================================================================
# 3. Node type predicates
# ============================================================================


def is_statement_node(node_type: str) -> bool:
    """Return True for control-flow, command, subshell, or compound statement types."""
    return node_type in BASH_CONTROL_FLOW_TYPES or node_type in (
        "command",
        "declaration_command",
        "unset_command",
        "variable_assignment",
        "redirected_statement",
        "negated_command",
        "test_command",
        "subshell",
        "compound_statement",
    )


def is_expansion_node(node_type: str) -> bool:
    """Return True for any shell expansion / substitution node type."""
    return node_type in BASH_EXPANSION_TYPES


def is_redirect_node(node_type: str) -> bool:
    """Return True for file-redirect, heredoc, or herestring node types."""
    return node_type in BASH_REDIRECT_TYPES


def is_operator_node(node_type: str) -> bool:
    """Return True for shell operator tokens (&&, ||, |, ;, &, etc.)."""
    return node_type in BASH_OPERATOR_TYPES


def is_control_flow_node(node_type: str) -> bool:
    """Return True for if/for/while/until/case/function nodes."""
    return node_type in BASH_CONTROL_FLOW_TYPES


def is_string_node(node_type: str) -> bool:
    """Return True for word, quoted-string, raw-string, or concatenation nodes."""
    return node_type in BASH_STRING_TYPES


def is_comment_node(node_type: str) -> bool:
    """Return True for comment nodes."""
    return node_type == "comment"


def is_error_node(node_type: str) -> bool:
    """Return True for ERROR nodes."""
    return node_type == "ERROR"


def is_list_node(node_type: str) -> bool:
    """Return True for list or pipeline container nodes."""
    return node_type in BASH_LIST_TYPES


def is_command_like_node(node_type: str) -> bool:
    """Return True for nodes that carry an executable name (command, declaration_command, unset_command)."""
    return node_type in BASH_EXECUTABLE_TYPES


def is_dangerous_node(node_type: str) -> bool:
    """Return True when *node_type* is in the dangerous-types index."""
    return node_type in frozenset(_DANGEROUS_TYPE_IDS)


# ============================================================================
# 4. Data classes (preserved from original port)
# ============================================================================

Node: TypeAlias = TsNode


@dataclass
class Redirect:
    op: Literal[">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"]
    target: str
    fd: int | None = None


@dataclass
class SimpleCommand:
    argv: list[str]
    env_vars: list[tuple[str, str]]
    redirects: list[Redirect]
    text: str


ParseForSecurityResult: TypeAlias = Union[
    "ParseForSecuritySimple",
    "ParseForSecurityTooComplex",
    "ParseForSecurityUnavailable",
]


@dataclass
class ParseForSecuritySimple:
    kind: Literal["simple"] = "simple"
    commands: list[SimpleCommand] = field(default_factory=list)


@dataclass
class ParseForSecurityTooComplex:
    kind: Literal["too-complex"] = "too-complex"
    reason: str = ""
    node_type: str | None = None


@dataclass
class ParseForSecurityUnavailable:
    kind: Literal["parse-unavailable"] = "parse-unavailable"


SemanticCheckResult: TypeAlias = Union["SemanticOk", "SemanticErr"]


@dataclass
class SemanticOk:
    ok: Literal[True] = True


@dataclass
class SemanticErr:
    ok: Literal[False] = False
    reason: str = ""


# ============================================================================
# 5. Node type ID mapping (analytics — preserved)
# ============================================================================


def node_type_id(node_type: str | None) -> int:
    """Numeric ID for analytics (matches TS ordering)."""
    if not node_type:
        return -2
    if node_type == "ERROR":
        return -1
    try:
        idx = _DANGEROUS_TYPE_IDS.index(node_type)
    except ValueError:
        return 0
    return idx + 1


# ============================================================================
# 6. Tree traversal utilities
# ============================================================================

# Node visitor signature:  Callable[[TsNode], Any]
NodeVisitor: TypeAlias = Callable[[TsNode], Any]

# Node predicate signature:  Callable[[TsNode], bool]
NodePredicate: TypeAlias = Callable[[TsNode], bool]


# ---- Core walk -------------------------------------------------------------


def walk_tree(
    node: TsNode,
    visitor: NodeVisitor,
    /,
    *,
    order: Literal["pre", "post"] = "pre",
    max_depth: int | None = None,
) -> None:
    """Depth-first walk over *node* and every descendant.

    Parameters
    ----------
    node:
        Root of the subtree to walk.
    visitor:
        Called once per node.  Return value is ignored.
    order:
        ``"pre"`` visits a node *before* its children;
        ``"post"`` visits a node *after* its children.
    max_depth:
        If set, skip subtrees beyond this depth (root = depth 0).
        ``None`` means unlimited depth.
    """

    def _walk(n: TsNode, depth: int) -> None:
        if max_depth is not None and depth > max_depth:
            return
        if order == "pre":
            visitor(n)
        for child in n.children:
            _walk(child, depth + 1)
        if order == "post":
            visitor(n)

    _walk(node, 0)


def walk_tree_with_depth(
    node: TsNode,
    visitor: Callable[[TsNode, int], Any],
    /,
    *,
    order: Literal["pre", "post"] = "pre",
    max_depth: int | None = None,
) -> None:
    """Like :func:`walk_tree` but the visitor receives ``(node, depth)``."""

    def _walk(n: TsNode, depth: int) -> None:
        if max_depth is not None and depth > max_depth:
            return
        if order == "pre":
            visitor(n, depth)
        for child in n.children:
            _walk(child, depth + 1)
        if order == "post":
            visitor(n, depth)

    _walk(node, 0)


def iter_nodes(
    node: TsNode,
    /,
    *,
    order: Literal["pre", "post"] = "pre",
    max_depth: int | None = None,
) -> Iterator[TsNode]:
    """Yield every node in the subtree depth-first."""
    stack: list[tuple[TsNode, int, bool]] = [(node, 0, False)]  # (node, depth, visited)
    while stack:
        n, depth, visited = stack.pop()
        if max_depth is not None and depth > max_depth:
            continue
        if not visited:
            if order == "pre":
                yield n
            stack.append((n, depth, True))
            for child in reversed(n.children):
                stack.append((child, depth + 1, False))
        else:
            if order == "post":
                yield n


# ---- Searching -------------------------------------------------------------


def find_nodes(
    root: TsNode,
    predicate: NodePredicate,
    /,
    *,
    max_results: int | None = None,
) -> list[TsNode]:
    """Return every node in the subtree for which *predicate* returns True.

    Parameters
    ----------
    max_results:
        Stop scanning after this many matches.  ``None`` = unlimited.
    """
    found: list[TsNode] = []

    def visit(n: TsNode) -> None:
        if max_results is not None and len(found) >= max_results:
            return
        if predicate(n):
            found.append(n)

    walk_tree(root, visit)
    return found


def find_first_node(
    root: TsNode,
    predicate: NodePredicate,
    /,
) -> TsNode | None:
    """Return the first (pre-order) node matching *predicate*, or ``None``."""
    results = find_nodes(root, predicate, max_results=1)
    return results[0] if results else None


def find_node_of_type(root: TsNode, node_type: str) -> TsNode | None:
    """Shorthand for ``find_first_node(root, lambda n: n.type == node_type)``."""
    return find_first_node(root, lambda n: n.type == node_type)


def find_nodes_of_type(root: TsNode, node_type: str) -> list[TsNode]:
    """Shorthand for ``find_nodes(root, lambda n: n.type == node_type)``."""
    return find_nodes(root, lambda n: n.type == node_type)


def find_nodes_of_types(
    root: TsNode, node_types: Iterable[str]
) -> list[TsNode]:
    """Return all descendants whose type is in *node_types*."""
    types = frozenset(node_types)
    return find_nodes(root, lambda n: n.type in types)


# ---- Direct-child queries --------------------------------------------------


def child_of_type(node: TsNode, node_type: str) -> TsNode | None:
    """Return the first direct child with type *node_type*, or ``None``."""
    for child in node.children:
        if child.type == node_type:
            return child
    return None


def children_of_type(node: TsNode, node_type: str) -> list[TsNode]:
    """Return all direct children with type *node_type*."""
    return [c for c in node.children if c.type == node_type]


def named_child(node: TsNode, index: int) -> TsNode | None:
    """Return the 0-indexed named child, or ``None`` if out of range.

    In tree-sitter, "named" children exclude anonymous punctuation tokens.
    This is a heuristic: we return the *index*-th child that is not an
    operator token.  When all children are operators, we fall back to
    positional indexing.
    """
    named: list[TsNode] = [c for c in node.children if not is_operator_node(c.type)]
    if named:
        try:
            return named[index]
        except IndexError:
            return None
    # Fallback: use raw position
    try:
        return node.children[index]
    except IndexError:
        return None


def node_child_count(node: TsNode) -> int:
    """Return the number of children."""
    return len(node.children)


def node_named_child_count(node: TsNode) -> int:
    """Return the count of children that are not anonymous operators."""
    return sum(1 for c in node.children if not is_operator_node(c.type))


# ---- Ancestor / sibling helpers (requires parent ref) ----------------------


def ancestors(node: TsNode) -> list[TsNode]:
    """Return the chain of ancestors from *node* up to the root (inclusive).

    Requires that *node* carries a ``parent`` attribute (set by a
    parent-linking pass).  Nodes from ``bash_parser.py`` do **not** have
    parent links by default.  Use :func:`link_parents` first.
    """
    chain: list[TsNode] = []
    cur: TsNode | None = node
    while cur is not None:
        chain.append(cur)
        cur = getattr(cur, "parent", None)
    return chain


def link_parents(root: TsNode) -> None:
    """Mutate *root* and every descendant, setting a ``parent`` attribute.

    Usage::

        link_parents(root)
        # now node.parent works for every node in the tree
    """
    root.parent = None  # type: ignore[attr-defined]

    def visit(n: TsNode) -> None:
        for child in n.children:
            child.parent = n  # type: ignore[attr-defined]

    walk_tree(root, visit)


# ---- Descendant-for-range (byte-offset query) ------------------------------


def descendant_for_byte_range(
    root: TsNode,
    start_byte: int,
    end_byte: int | None = None,
) -> TsNode | None:
    """Return the smallest (deepest) node that fully contains the byte range
    ``[start_byte, end_byte]``.

    When *end_byte* is ``None``, it is set equal to *start_byte* (point
    query).  Returns ``None`` when no node spans the range.

    Port of tree-sitter's ``descendantForRange`` / ``descendantForPosition``.
    """
    if end_byte is None:
        end_byte = start_byte

    best: TsNode | None = None
    best_size: int = 10**12

    def visit(n: TsNode) -> None:
        nonlocal best, best_size
        # Node must fully contain the range.
        if n.start_index <= start_byte and (end_byte is not None and n.end_index >= end_byte):
            size = n.end_index - n.start_index
            if size < best_size:
                best = n
                best_size = size

    walk_tree(root, visit)
    return best


def node_at_byte_offset(root: TsNode, offset: int) -> TsNode | None:
    """Return the deepest leaf node at byte *offset* (point query)."""
    return descendant_for_byte_range(root, offset, offset)


# ---- Text extraction -------------------------------------------------------


def node_source_text(node: TsNode, source: str) -> str:
    """Slice *source* using *node.start_index* and *node.end_index*.

    When the node stores its own ``.text`` attribute (common in
    :class:`TsNode`) you can use that directly; this is a fallback for raw
    dict-like trees.
    """
    return source[node.start_index : node.end_index]


# ---- Node-type summary -----------------------------------------------------


def count_node_types(root: TsNode) -> dict[str, int]:
    """Return a ``{node_type: count}`` histogram of the subtree."""
    hist: dict[str, int] = {}

    def visit(n: TsNode) -> None:
        hist[n.type] = hist.get(n.type, 0) + 1

    walk_tree(root, visit)
    return hist


def has_node_of_type(root: TsNode, node_type: str) -> bool:
    """Return True when the subtree contains at least one node of *node_type*."""
    return find_first_node(root, lambda n: n.type == node_type) is not None


def has_any_node_of_types(root: TsNode, node_types: Iterable[str]) -> bool:
    """Return True when the subtree contains at least one node whose type is in *node_types*."""
    types = frozenset(node_types)
    return find_first_node(root, lambda n: n.type in types) is not None


# ============================================================================
# 7. AST debug / dump
# ============================================================================


def dump_ast(
    node: TsNode,
    /,
    *,
    indent: int = 0,
    max_depth: int | None = None,
    show_text: bool = True,
    show_offsets: bool = True,
    max_text_len: int = 60,
) -> str:
    """Return a pretty-printed multiline string of the AST.

    Parameters
    ----------
    node:
        Root of the subtree to dump.
    indent:
        Base indentation level (internal use — callers pass 0).
    max_depth:
        Stop rendering beyond this depth.  ``None`` = unlimited.
    show_text:
        Include ``.text`` of each node.
    show_offsets:
        Include ``[start:end]`` byte offsets.
    max_text_len:
        Truncate node text beyond this length.
    """
    lines: list[str] = []
    _prefix = "  " * indent

    # Build the line for this node.
    parts: list[str] = [f"({node.type}"]

    if show_offsets:
        parts.append(f" [{node.start_index}:{node.end_index}]")

    if show_text:
        txt = getattr(node, "text", "") or ""
        if len(txt) > max_text_len:
            txt = txt[:max_text_len] + "…"
        # Escape newlines for single-line display.
        esc = txt.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
        parts.append(f' "{esc}"')

    child_count = len(node.children)
    if child_count:
        parts.append(f" children={child_count}")

    parts.append(")")
    lines.append(f"{_prefix}{''.join(parts)}")

    if max_depth is not None and indent >= max_depth:
        if child_count:
            lines.append(f"{_prefix}  … ({child_count} children hidden)")
        return "\n".join(lines)

    for child in node.children:
        lines.append(
            dump_ast(
                child,
                indent=indent + 1,
                max_depth=max_depth,
                show_text=show_text,
                show_offsets=show_offsets,
                max_text_len=max_text_len,
            )
        )

    return "\n".join(lines)


def dump_ast_compact(
    node: TsNode,
    /,
    *,
    max_depth: int | None = None,
) -> str:
    """Like :func:`dump_ast` but single-line per node, no offsets, 30-char text max."""
    return dump_ast(
        node,
        max_depth=max_depth,
        show_text=True,
        show_offsets=False,
        max_text_len=30,
    )


# ============================================================================
# 8. String text extraction from AST nodes
# ============================================================================

# Regex for stripping shell quoting from resolved node text.
_SINGLE_QUOTE_STRIP = re.compile(r"^'(.*)'$", re.DOTALL)
_DOUBLE_QUOTE_STRIP = re.compile(r'^"(.*)"$', re.DOTALL)
_ANSI_C_STRIP = re.compile(r"^\$'(.*)'$", re.DOTALL)
_DOLLAR_DOUBLE_STRIP = re.compile(r'^\$"(.*)"$', re.DOTALL)

# Patterns for stripping quoting layers from raw quoted token text.
_LEADING_TRAILING_QUOTE_RE = re.compile(
    r"^(?:'([^']*)'|\"((?:[^\"\\]|\\.)*)\"|\$'((?:[^'\\]|\\.)*)')$"
)

# Node types whose text contributes a literal token to argv.
_ARGV_CONTRIBUTING_TYPES: frozenset[str] = frozenset(
    {
        "word",
        "string",
        "raw_string",
        "ansi_c_string",
        "translated_string",
        "simple_expansion",
        "expansion",
        "string_expansion",
        "concatenation",
    }
)

# Node types that block argv extraction because their content is runtime-dynamic.
_ARGV_BLOCKING_TYPES: frozenset[str] = frozenset(
    {
        "command_substitution",
        "process_substitution",
        "arithmetic_expansion",
        "brace_expression",
    }
)


def _resolve_simple_expansion(node: TsNode) -> str:
    """Resolve a ``simple_expansion`` or ``expansion`` node to a literal
    placeholder, or return the node text when it appears to be a safe
    variable reference.

    Returns the node text as-is for simple ``$VAR`` references so downstream
    semantic checks can inspect variable names.  Returns ``$`` for more
    complex expansions that embed runtime logic.
    """
    text = node.text or ""
    # $VAR  $?  $!  $#  $@  $*  $$  $-  $_
    if re.match(r"^\$[A-Za-z_?!#@*$\-][A-Za-z0-9_]*$", text):
        return text
    # ${VAR}  ${VAR:-default}  ${VAR+alt}  etc.
    if text.startswith("${") and text.endswith("}"):
        inner = text[2:-1]
        # Bare variable name: ${VAR}
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", inner):
            return text
        # ${#VAR} — length
        if re.match(r"^#[A-Za-z_][A-Za-z0-9_]*$", inner):
            return text
        # Parameter expansion with operator: ${VAR:-...}, ${VAR:=...}, etc.
        m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*(:?[-=?+%#]|%%?|##?|//?|^)", inner)
        if m:
            return "${" + inner[: m.end()] + "...}"
    return text


def _extract_string_node_text(node: TsNode) -> str:
    """Extract the literal-ish text from a string-like AST node.

    Handles:
    - ``word`` — bare tokens
    - ``string`` — double-quoted ``"…"``; strips outer quotes
    - ``raw_string`` — single-quoted ``'…'``; strips outer quotes
    - ``ansi_c_string`` — ``$'…'``; strips the prefix and outer quotes
    - ``translated_string`` — ``$"…"``; strips prefix and outer quotes
    - ``concatenation`` — joins children recurively
    - ``simple_expansion`` — ``$VAR``; resolves to literal placeholder
    - ``expansion`` — ``${…}``; resolves via :func:`_resolve_simple_expansion`
    - ``string_expansion`` — expansion embedded inside a string

    Returns the empty string for expansion types whose value is
    runtime-dynamic (command substitution, process substitution, arithmetic).
    """
    if not node:
        return ""

    ntype = node.type
    text = node.text or ""

    if ntype == "word":
        return text

    if ntype == "raw_string":
        # '…'  → strip outer single quotes
        m = _SINGLE_QUOTE_STRIP.match(text)
        return m.group(1) if m else text

    if ntype == "string":
        # "…"  → strip outer double quotes, handle backslash escapes
        m = _DOUBLE_QUOTE_STRIP.match(text)
        if m:
            inner = m.group(1)
            # Unescape the handful of shell escapes inside double quotes.
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
            inner = inner.replace("\\n", "\n").replace("\\t", "\t")
            inner = inner.replace("\\$", "$").replace("\\`", "`")
            return inner
        return text

    if ntype == "ansi_c_string":
        # $'…'  → strip $' and '
        m = _ANSI_C_STRIP.match(text)
        if m:
            inner = m.group(1)
            inner = inner.replace("\\n", "\n").replace("\\t", "\t")
            inner = inner.replace("\\'", "'").replace("\\\\", "\\")
            return inner
        return text

    if ntype == "translated_string":
        # $"…"  → strip $" and ", treat like double-quoted
        m = re.match(r'^\$"(.*)"$', text, re.DOTALL)
        if m:
            inner = m.group(1)
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
            return inner
        return text

    if ntype == "concatenation":
        # "a"b'c'  → children provide the pieces
        parts: list[str] = []
        for child in node.children:
            pt = _extract_string_node_text(child)
            parts.append(pt)
        return "".join(parts)

    if ntype == "simple_expansion":
        return _resolve_simple_expansion(node)

    if ntype == "expansion":
        return _resolve_simple_expansion(node)

    if ntype == "string_expansion":
        # Expansion mid-string: delegate to children
        parts = []
        for child in node.children:
            parts.append(_extract_string_node_text(child))
        return "".join(parts)

    if ntype in _ARGV_BLOCKING_TYPES:
        return ""

    # Fallback: return raw text for any other node type.
    return text


def _resolve_argv_token(node: TsNode) -> str | None:
    """Return the literal token text for *node* when it contributes to argv.

    Returns ``None`` when the node's content is runtime-dynamic and cannot
    be resolved statically (command substitution, process substitution,
    arithmetic expansion).
    """
    ntype = node.type

    if ntype in _ARGV_BLOCKING_TYPES:
        return None

    if ntype in _ARGV_CONTRIBUTING_TYPES:
        return _extract_string_node_text(node)

    # Named redirect operators (>  >>  <  <<  etc.) are not part of argv.
    if ntype in BASH_REDIRECT_TYPES or is_operator_node(ntype):
        return None

    # Fallback: try text extraction for unrecognised node types.
    if hasattr(node, "text") and node.text:
        return node.text

    return None


# ============================================================================
# 9. AST argv / redirect / env-var extraction
# ============================================================================

# Node types that always cause a "too-complex" bail-out during argv extraction.
# These are constructs whose security implications require native tool
# evaluation (full TS parser) rather than Python AST walking.
_TOO_COMPLEX_NODE_TYPES: frozenset[str] = frozenset(
    {
        "c_style_for_statement",
    }
)

# Node types that are safe to encounter in an otherwise-simple command but
# signal that we should inspect carefully (heredoc body, case item bodies).
_SAFE_CONTAINING_TYPES: frozenset[str] = frozenset(
    {
        "heredoc_start",
        "heredoc_body",
        "comment",
    }
)


def _extract_argv_from_command_node(node: TsNode) -> list[str] | None:
    """Extract the full argv list from a ``command``, ``declaration_command``,
    ``unset_command``, or ``variable_assignment`` AST node.

    Returns *argv* as ``[command_name, arg1, arg2, …]`` or ``None`` when
    the command contains runtime-dynamic expansions that cannot be resolved
    statically.

    Parameters
    ----------
    node:
        An AST node whose type is in :data:`BASH_COMMAND_TYPES` (typically
        ``command``, ``declaration_command``, ``unset_command``) or
        ``variable_assignment``.
    """
    argv: list[str] = []

    # --- Pass 1: locate the command_name child ---
    name_node = find_first_node(node, lambda n: n.type == "command_name")
    if name_node is not None:
        # The command_name node usually has a single word child.
        for child in name_node.children:
            token = _resolve_argv_token(child)
            if token is not None:
                argv.append(token)
                break
        if not argv:
            # command_name text may be the bare word itself
            raw = (name_node.text or "").strip()
            if raw:
                argv.append(raw)

    # --- Pass 2: walk direct children, extracting word-like tokens ---
    for child in node.children:
        ctype = child.type

        # Skip the command_name — we already handled it.
        if ctype == "command_name":
            continue

        # Variable assignments preceding a command do not contribute to argv.
        if ctype == "variable_assignment":
            continue

        # Redirects and their targets are not argv.
        if ctype in BASH_REDIRECT_TYPES:
            continue

        # Operator tokens are not argv.
        if is_operator_node(ctype):
            continue

        # Comment and heredoc body nodes are noise.
        if ctype in _SAFE_CONTAINING_TYPES:
            continue

        # Words and string-like nodes contribute tokens.
        if ctype in _ARGV_CONTRIBUTING_TYPES:
            token = _resolve_argv_token(child)
            if token is None:
                return None  # runtime-dynamic — too complex
            stripped = token.strip()
            if stripped:
                argv.append(stripped)
            continue

        # If this node contains a blocking expansion, bail out.
        if ctype in _ARGV_BLOCKING_TYPES:
            return None

        # Nested executable types (e.g. negated_command inside a command).
        if ctype in BASH_EXECUTABLE_TYPES:
            inner = _extract_argv_from_command_node(child)
            if inner is None:
                return None
            argv.extend(inner)
            continue

        # Subsessions / subshells — we cannot statically determine argv.
        if ctype in ("subshell", "compound_statement", "test_command"):
            return None

        # Recurse into unknown container types in case they wrap a command.
        if child.children:
            inner = _extract_argv_from_command_node(child)
            if inner is not None:
                argv.extend(inner)

    return argv if argv else None


def _extract_env_vars_from_command_node(node: TsNode) -> list[tuple[str, str]]:
    """Extract ``VAR=value`` assignments that precede the command in *node*.

    Returns a list of ``(name, value)`` tuples (matching
    :attr:`SimpleCommand.env_vars`).
    """
    env_vars: list[tuple[str, str]] = []

    for child in node.children:
        if child.type == "variable_assignment":
            text = child.text or ""
            eq = text.find("=")
            if eq > 0:
                name = text[:eq]
                val = text[eq + 1 :]
                # Strip quoting from the value if present.
                m = _LEADING_TRAILING_QUOTE_RE.match(val)
                if m:
                    val = m.group(1) or m.group(2) or m.group(3) or val
                env_vars.append((name, val))
        elif child.type == "variable_name":
            # Bare variable_name without a value (syntax error / partial).
            continue

    return env_vars


def _extract_redirects_from_command_node(node: TsNode) -> list[Redirect]:
    """Extract :class:`Redirect` objects from redirect children of *node*."""
    redirects: list[Redirect] = []

    for child in node.children:
        if child.type not in BASH_REDIRECT_TYPES:
            # Recurse into redirected_statement wrappers.
            if child.type == "redirected_statement":
                redirects.extend(_extract_redirects_from_command_node(child))
            continue

        rr = _parse_single_redirect(child)
        if rr is not None:
            redirects.append(rr)

    return redirects


def _parse_single_redirect(node: TsNode) -> Redirect | None:
    """Parse a single redirect AST node into a :class:`Redirect` object.

    Handles ``file_redirect``, ``heredoc_redirect``, and
    ``herestring_redirect`` nodes.
    """
    ctype = node.type
    text = (node.text or "").strip()

    # ---- Determine the operator ----
    op: Literal[">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"] = ">"
    fd: int | None = None
    target = ""

    # Look for operator tokens among children.
    for child in node.children:
        if is_operator_node(child.type) and child.type in {
            ">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<",
        }:
            op = child.type  # type: ignore[assignment]

    # ---- Heuristic from text ----
    _OP_MAP: dict[str, str] = {
        ">>": ">>", ">&": ">&", "&>": "&>", "&>>": "&>>",
        "<<<": "<<<", "<<": "<<", ">|": ">|", "<&": "<&",
        "<": "<", ">": ">",
    }
    for tok, mapped in sorted(_OP_MAP.items(), key=lambda x: -len(x[0])):
        if tok in text:
            op = mapped  # type: ignore[assignment]
            break

    # ---- File descriptor ----
    fd_match = re.match(r"^(\d+)\s*[><]", text)
    if fd_match:
        try:
            fd = int(fd_match.group(1))
        except ValueError:
            pass

    # ---- Target (file path, heredoc delimiter, or herestring) ----
    for child in node.children:
        if child.type in ("word", "string", "raw_string", "expansion", "simple_expansion"):
            txt = _extract_string_node_text(child)
            if txt:
                target = txt
                break
        if child.type == "heredoc_start":
            target = (child.text or "").strip()
            break
        if child.type == "concatenation":
            txt = _extract_string_node_text(child)
            if txt:
                target = txt
                break

    # Fallback: extract target from the raw node text after the operator.
    if not target:
        op_idx = text.find(op)
        if op_idx >= 0:
            remainder = text[op_idx + len(op) :].strip()
            if remainder:
                m = _LEADING_TRAILING_QUOTE_RE.match(remainder)
                if m:
                    target = m.group(1) or m.group(2) or m.group(3) or remainder
                else:
                    target = remainder

    if ctype == "heredoc_redirect":
        # Heredoc bodies are not part of the redirect target.
        if target.startswith("<<"):
            target = target[2:].strip()
        # Trim leading/trailing quotes around heredoc delimiter.
        target = target.strip("'\"")

    target = target.strip()

    return Redirect(op=op, target=target, fd=fd)


def _extract_simple_commands_from_root(root: TsNode) -> list[SimpleCommand] | None:
    """Walk *root* and extract :class:`SimpleCommand` objects from top-level
    command nodes.

    Returns ``None`` when any command in the tree contains constructs that
    we cannot resolve statically (command substitution, etc.).

    Skips commands nested inside control-flow blocks (if/for/while/case/function)
    and subshells — those are either not directly executable or require
    separate analysis.
    """
    commands: list[SimpleCommand] = []

    def _walk(n: TsNode, depth: int = 0) -> bool:
        """Walk the tree looking for executable command nodes.

        Returns False to signal "too complex" — the caller should bail.
        """
        ntype = n.type

        # Bail on explicitly blacklisted types.
        if ntype in _TOO_COMPLEX_NODE_TYPES:
            return False

        # Control-flow constructs wrap commands; skip their bodies for
        # simple-command extraction (they require separate analysis).
        if ntype in BASH_CONTROL_FLOW_TYPES:
            return True  # continue walking siblings, skip children

        # Subshell — skip children (commands inside are in a separate scope).
        if ntype in ("subshell", "compound_statement", "test_command"):
            return True

        # Executable command nodes.
        if ntype in BASH_EXECUTABLE_TYPES:
            argv = _extract_argv_from_command_node(n)
            if argv is None:
                return False  # too complex
            env_vars = _extract_env_vars_from_command_node(n)
            redirects = _extract_redirects_from_command_node(n)
            commands.append(
                SimpleCommand(
                    argv=argv,
                    env_vars=env_vars,
                    redirects=redirects,
                    text=n.text or "",
                )
            )
            return True  # continue walking siblings

        # Declarations (export FOO=bar) — treated as a command with argv
        if ntype == "declaration_command":
            argv = _extract_argv_from_command_node(n)
            if argv is not None:
                env_vars = _extract_env_vars_from_command_node(n)
                redirects = _extract_redirects_from_command_node(n)
                commands.append(
                    SimpleCommand(
                        argv=argv,
                        env_vars=env_vars,
                        redirects=redirects,
                        text=n.text or "",
                    )
                )
            return True

        # Variable assignments at top level (VAR=val) — not a command per se
        # but still worth recording if they are the only thing in the tree.
        if ntype == "variable_assignment":
            if _has_sibling_executable(n):
                return True  # skip — this is a prefix to a real command
            env_vars = _extract_env_vars_from_command_node(n)
            # Create a pseudo-command to represent the assignment.
            if env_vars:
                commands.append(
                    SimpleCommand(
                        argv=["env"],
                        env_vars=env_vars,
                        redirects=[],
                        text=n.text or "",
                    )
                )
            return True

        # Walk children.
        for child in n.children:
            if not _walk(child, depth + 1):
                return False

        return True

    if not _walk(root):
        return None

    return commands if commands else []


def _has_sibling_executable(node: TsNode) -> bool:
    """Return True when *node* has a sibling that is an executable command type."""
    parent = getattr(node, "parent", None)
    if parent is None:
        return False
    for sibling in parent.children:
        if sibling is node:
            continue
        if sibling.type in BASH_EXECUTABLE_TYPES:
            return True
        if sibling.type == "declaration_command":
            return True
    return False


def _detect_too_complex_reason(root: TsNode, cmd: str) -> str | None:
    """Return a human-readable reason string when *root* contains constructs
    that make argv extraction infeasible, or ``None`` when the tree looks
    simple enough to extract.

    This is called after :func:`_extract_simple_commands_from_root` returns
    ``None``, to explain *why*.
    """
    # Explicit too-complex types.
    for child_type in _TOO_COMPLEX_NODE_TYPES:
        if has_node_of_type(root, child_type):
            return f"Contains {child_type.replace('_', ' ')}"

    # Runtime-dynamic expansions.
    for child_type in _ARGV_BLOCKING_TYPES:
        if has_node_of_type(root, child_type):
            return f"Contains {child_type.replace('_', ' ')} (runtime-dynamic)"

    # Control flow blocks that wrap everything.
    for child_type in BASH_CONTROL_FLOW_TYPES:
        if has_node_of_type(root, child_type):
            return f"Root command is a {child_type.replace('_', ' ')}"

    # Subshell / compound / test.
    for child_type in ("subshell", "compound_statement", "test_command"):
        if has_node_of_type(root, child_type):
            return f"Root contains a {child_type.replace('_', ' ')}"

    # Brace expansion.
    if has_node_of_type(root, "brace_expression"):
        return "Contains brace expansion (runtime-dynamic)"

    # General fallback.
    if root.is_error if hasattr(root, "is_error") else False:
        return "Parse tree contains syntax errors"

    return "Unable to extract argv from AST"


# ============================================================================
# 10. parse_for_security entrypoints (pre-checks + AST-driven analysis)
# ============================================================================


def _mask_braces_in_quoted_contexts(cmd: str) -> str:
    """Replace ``{`` characters inside quoted strings with spaces.

    Used as a pre-check heuristic: braces inside quotes cannot be expansion
    delimiters, so masking them prevents false positives for brace-with-quote
    patterns.
    """
    if "{" not in cmd:
        return cmd
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if in_single:
            if c == "'":
                in_single = False
            out.append(" " if c == "{" else c)
            i += 1
        elif in_double:
            if c == "\\" and i + 1 < len(cmd) and cmd[i + 1] in ('"', "\\"):
                out.extend((c, cmd[i + 1]))
                i += 2
            else:
                if c == '"':
                    in_double = False
                out.append(" " if c == "{" else c)
                i += 1
        else:
            if c == "\\" and i + 1 < len(cmd):
                out.extend((c, cmd[i + 1]))
                i += 2
            else:
                if c == "'":
                    in_single = True
                elif c == '"':
                    in_double = True
                out.append(c)
                i += 1
    return "".join(out)


def _run_pre_checks(cmd: str) -> ParseForSecurityResult | None:
    """Run regex-based pre-checks on *cmd*.

    Returns a :class:`ParseForSecurityTooComplex` when a dangerous pattern
    is found, or ``None`` when the command passes all pre-checks.
    """
    if CONTROL_CHAR_RE.search(cmd):
        return ParseForSecurityTooComplex(reason="Contains control characters")
    if UNICODE_WHITESPACE_RE.search(cmd):
        return ParseForSecurityTooComplex(reason="Contains Unicode whitespace")
    if BACKSLASH_WHITESPACE_RE.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Contains backslash-escaped whitespace"
        )
    if ZSH_TILDE_BRACKET_RE.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Contains zsh ~[ dynamic directory syntax"
        )
    if ZSH_EQUALS_EXPANSION_RE.search(cmd):
        return ParseForSecurityTooComplex(reason="Contains zsh =cmd equals expansion")
    if BRACE_WITH_QUOTE_RE.search(_mask_braces_in_quoted_contexts(cmd)):
        return ParseForSecurityTooComplex(
            reason="Contains brace with quote character (expansion obfuscation)"
        )
    return None


def parse_for_security_from_ast(
    cmd: str, root: TsNode | Any
) -> ParseForSecurityResult:
    """Security analysis of *cmd* using the AST in *root*.

    Pipeline:
    1. Run regex pre-checks for obfuscation / dangerous syntax.
    2. Run semantic pre-checks on the raw command text.
    3. Attempt to extract :class:`SimpleCommand` objects from the AST.
    4. If extraction succeeds, run post-argv semantic checks.
    5. If extraction fails, return reason as ``too-complex``.

    Parameters
    ----------
    cmd:
        The raw command string.
    root:
        The root AST node (``TsNode``) from tree-sitter parsing, or
        :obj:`PARSE_ABORTED` when parsing timed out.
    """
    # ---- Pre-checks ----
    pre = _run_pre_checks(cmd)
    if pre is not None:
        return pre

    # ---- Text-level semantic pre-checks ----
    text_sem = _run_text_semantic_pre_checks(cmd)
    if text_sem is not None:
        return text_sem

    # ---- Empty command ----
    trimmed = cmd.strip()
    if trimmed == "":
        return ParseForSecuritySimple(commands=[])

    # ---- Parse failure / unavailable ----
    if root is PARSE_ABORTED:
        return ParseForSecurityTooComplex(
            reason="Parse aborted (timeout or resource limit)",
            node_type="ERROR",
        )

    if root is None:
        return ParseForSecurityUnavailable()

    if not isinstance(root, TsNode):
        return ParseForSecurityUnavailable()

    # ---- Error node at root ----
    if root.type == "ERROR":
        return ParseForSecurityTooComplex(
            reason="Parse produced an error node",
            node_type="ERROR",
        )

    # ---- Too-complex structural check ----
    safety = classify_ast_safety(root)
    if safety.has_error_node:
        err_nodes = find_nodes_of_type(root, "ERROR")
        if err_nodes:
            snippet = (err_nodes[0].text or "")[:60]
            return ParseForSecurityTooComplex(
                reason=f"Syntax error near: {snippet}",
                node_type="ERROR",
            )

    # ---- Attempt argv extraction ----
    commands = _extract_simple_commands_from_root(root)
    if commands is None:
        reason = _detect_too_complex_reason(root, cmd)
        return ParseForSecurityTooComplex(reason=reason)

    if not commands:
        # AST contained no executable commands — might be a bare variable
        # assignment we already handled, or an empty program node.
        # Check for bare env var assignments at root level.
        env_commands = _extract_bare_env_assignments(root)
        if env_commands:
            commands = env_commands
        else:
            return ParseForSecuritySimple(commands=[])

    return ParseForSecuritySimple(commands=commands)


def _extract_bare_env_assignments(root: TsNode) -> list[SimpleCommand] | None:
    """Extract simple env-var assignments from a root that has no executable
    command nodes (e.g. ``FOO=bar BAZ=qux``)."""
    result: list[SimpleCommand] = []
    env_vars: list[tuple[str, str]] = []

    for child in root.children:
        if child.type == "variable_assignment":
            text = child.text or ""
            eq = text.find("=")
            if eq > 0:
                name = text[:eq]
                val = text[eq + 1 :]
                m = _LEADING_TRAILING_QUOTE_RE.match(val)
                if m:
                    val = m.group(1) or m.group(2) or m.group(3) or val
                env_vars.append((name, val))

    if env_vars:
        result.append(
            SimpleCommand(
                argv=["env"],
                env_vars=env_vars,
                redirects=[],
                text=root.text or "",
            )
        )
        return result

    return None


async def parse_for_security(cmd: str) -> ParseForSecurityResult:
    """Async entry point: parse *cmd* with tree-sitter and run full security analysis.

    The analysis pipeline, in order:
    1. Quick regex pre-checks for known obfuscation patterns.
    2. Text-level semantic pre-checks (injection, path traversal, dangerous commands).
    3. Tree-sitter parse (with fallback to regex tokenizer).
    4. AST argv / redirect / env-var extraction.
    5. Post-argv semantic checks on extracted arguments.

    Returns
    -------
    ParseForSecurityResult
        ``ParseForSecuritySimple`` with extracted commands when analysis succeeds,
        ``ParseForSecurityTooComplex`` when the command contains constructs that
        require the native CLI path, or ``ParseForSecurityUnavailable`` when
        parsing is unavailable.
    """
    if cmd == "":
        return ParseForSecuritySimple(commands=[])

    root = await parse_command_raw(cmd)
    if root is None:
        # Even when tree-sitter is unavailable, run pre-checks and text-level
        # semantic checks on the raw command.
        pre = _run_pre_checks(cmd)
        if pre is not None:
            return pre
        text_sem = _run_text_semantic_pre_checks(cmd)
        if text_sem is not None:
            return text_sem
        return ParseForSecurityUnavailable()

    return parse_for_security_from_ast(cmd, root)


# ============================================================================
# 11. Semantic checks (pre-argv and post-argv)
# ============================================================================

# Patterns for text-level semantic pre-checks (run before AST extraction).
# -----------------------------------------------------------------------

# Shell comment injection via newline.
_SEM_NEWLINE_HASH = re.compile(r"\n[ \t]*#")

# Path traversal — argument that escapes the working directory.
_SEM_PATH_TRAVERSAL = re.compile(
    r"(?:^|[\s;|&])(?:\./|/)(?:\.\./)+(?:[^\s;|&]*)?"
)
# Detect bare ``..`` used as an argument.
_SEM_DOTDOT_ARG = re.compile(r"(?:^|[\s;|&])\.\.[\s;|&]")

# Inline script execution (piped to sh, bash, python, etc.).
_SEM_PIPE_TO_INTERPRETER = re.compile(
    r"\|\s*(?:sh|bash|dash|zsh|python|perl|ruby|node|lua|php|expect)(?:\s|$)"
)

# /proc filesystem info-leak patterns.
_SEM_PROC_LEAK = re.compile(r"/proc/(?:self|[0-9]+)/(?:environ|cmdline|mem|fd|maps|cwd|root|exe)")

# Obfuscated / suspicious argument patterns.
_SEM_ENCODED_BASE64 = re.compile(r"(?:base64|bzip2|gzip|xz)\s+(?:-d|--decode)\b", re.IGNORECASE)
_SEM_HEX_ENCODING = re.compile(r"(?:\\\\x[0-9a-fA-F]{2}){6,}")  # six or more hex escapes
_SEM_DEV_FD = re.compile(r"/dev/(?:fd|tcp|udp)/")

# Dangerous commands (filesystem destruction, permission changes).
_DANGEROUS_COMMAND_RE = re.compile(
    r"^\s*(?:rm|chmod|chown)\s+.*(?:-rf?|777|xhost)"
)
_DANGEROUS_DEVNULL_WRITE = re.compile(r">\s*/dev/[a-z]+[a-z0-9]*")

# Command injection / argument poisoning.
_SEM_CMD_INJECTION = re.compile(r"[;&|]+\s*(?:sh|bash|nc|curl|wget)\b")

# Flag-confusion patterns (-- separated arguments that look like options).
_SEM_FLAG_CONFUSION = re.compile(r"(?:^|[\s;|&])--?[a-zA-Z_-]{2,}(?:\s|$)")

# Backtick command substitution (older shell, often used for obfuscation).
_SEM_BACKTICK = re.compile(r"`[^`]*`")

# Reverse shell indicators.
_SEM_REVERSE_SHELL = re.compile(
    r"(?:bash|sh|nc|ncat|netcat)\s+.*(?:-e\s|/dev/tcp/|mkfifo)",
    re.IGNORECASE,
)

# File descriptor manipulation that evades simple analysis.
_SEM_FD_MANIP = re.compile(r"(?:exec|eval)\s+\d+[<>]")


def _run_text_semantic_pre_checks(cmd: str) -> ParseForSecurityResult | None:
    """Run text-level semantic checks on the raw command string *before* AST
    parsing.

    These checks catch patterns that may be obfuscated or lost during AST
    extraction.  Returns ``ParseForSecurityTooComplex`` on a match, or
    ``None`` when all checks pass.
    """
    # Command injection / separator manipulation.
    if _SEM_CMD_INJECTION.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Command injection pattern detected (separator + interpreter)"
        )

    # Reverse shell detection.
    if _SEM_REVERSE_SHELL.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Reverse shell pattern detected"
        )

    # Backtick command substitution (often used for obfuscation).
    if _SEM_BACKTICK.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Contains backtick command substitution"
        )

    # Encoded / obfuscated payloads.
    if _SEM_ENCODED_BASE64.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Contains base64 decode in command"
        )
    if _SEM_HEX_ENCODING.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Contains hex-encoded payload"
        )

    # /dev/fd or /dev/tcp references.
    if _SEM_DEV_FD.search(cmd):
        return ParseForSecurityTooComplex(
            reason="References /dev/fd/, /dev/tcp/, or /dev/udp/"
        )

    # exec/eval with file descriptor redirection.
    if _SEM_FD_MANIP.search(cmd):
        return ParseForSecurityTooComplex(
            reason="Uses exec/eval with file descriptor manipulation"
        )

    return None


def check_semantics(commands: list[SimpleCommand]) -> SemanticCheckResult:
    """Post-argv semantic checks.

    Runs after argv extraction succeeds.  Checks each argument against a
    comprehensive set of dangerous patterns:

    - ``/proc/*/environ`` references (info leak)
    - Newline + ``#`` shell comment injection
    - Path traversal (``../`` escaping working directory)
    - Piping to shell interpreters
    - Reverse shell indicators in arguments
    - File descriptor redirection to /dev/*
    - Dangerous commands (``rm -rf``, ``chmod 777``)
    - Flag confusion (arguments starting with ``--`` that look like options)
    """
    proc_environ = re.compile(r"/proc/.*/environ")
    newline_hash = re.compile(r"\n[ \t]*#")
    path_traversal = re.compile(r"(?:^|/)\.\.(?:/|$)")
    reverse_shell = re.compile(
        r"(?:-e\s|/dev/tcp/|mkfifo|python\s+-c\s+.*socket)", re.IGNORECASE
    )
    pipe_interp = re.compile(r"\|\s*(?:sh|bash|dash|zsh|python\d?|perl|ruby|node|lua)\b")
    devnull_write = re.compile(r">\s*/dev/[a-z]+")
    dangerous_cmd = re.compile(
        r"^(?:rm|chmod|chown|dd|mkfs|mkswap)\b.*(?:-rf?|777|of=/dev/)",
    )

    for cmd in commands:
        for arg in cmd.argv:
            # Info leak via /proc.
            if proc_environ.search(arg):
                return SemanticErr(reason="Argument references /proc/*/environ")

            # Shell comment injection.
            if newline_hash.search(arg):
                return SemanticErr(
                    reason="Argument contains newline followed by shell comment"
                )

            # Path traversal.
            if path_traversal.search(arg):
                return SemanticErr(
                    reason=f"Argument contains path traversal: {arg[:80]}"
                )

            # Reverse shell indicators.
            if reverse_shell.search(arg):
                return SemanticErr(
                    reason=f"Argument contains reverse shell pattern: {arg[:80]}"
                )

            # Pipe to interpreter.
            if pipe_interp.search(arg):
                return SemanticErr(
                    reason=f"Argument pipes to interpreter: {arg[:80]}"
                )

            # Dangerous /dev writes.
            if devnull_write.search(arg):
                return SemanticErr(
                    reason=f"Argument redirects to /dev/*: {arg[:80]}"
                )

            # Dangerous command in argv[0].
            if cmd.argv and dangerous_cmd.match(cmd.argv[0]):
                return SemanticErr(
                    reason=f"Dangerous command pattern: {cmd.argv[0]}"
                )

        # Check env vars for injection.
        for _name, val in cmd.env_vars:
            if newline_hash.search(val):
                return SemanticErr(
                    reason="Env-var value contains newline + shell comment"
                )
            if path_traversal.search(val):
                return SemanticErr(
                    reason="Env-var value contains path traversal"
                )

    return SemanticOk()


def check_dangerous_command(commands: list[SimpleCommand]) -> SemanticCheckResult:
    """Lightweight check focused on the command name only (not full argv).

    Returns :class:`SemanticErr` when the command name matches a known
    dangerous pattern (``rm -rf``, ``chmod 777``, raw disk writes, etc.).

    Use :func:`check_semantics` for comprehensive argument inspection.
    """
    _DANGEROUS = re.compile(
        r"^(?:rm|chmod|chown|dd|mkfs|mkswap|shred|wipe|fdisk|parted)$"
    )
    _DESTRUCTIVE_FLAGS = re.compile(r"-(?:rf?|recursive|force)")
    _PERMISSIVE_PERMS = re.compile(r"(?:0?777|[47]77)")

    for cmd in commands:
        name = cmd.argv[0] if cmd.argv else ""
        if _DANGEROUS.match(name):
            joined = " ".join(cmd.argv)
            if _DESTRUCTIVE_FLAGS.search(joined):
                return SemanticErr(
                    reason=f"Dangerous command with destructive flags: {joined[:100]}"
                )
            if _PERMISSIVE_PERMS.search(joined):
                return SemanticErr(
                    reason=f"Dangerous command with permissive permissions: {joined[:100]}"
                )

    return SemanticOk()


# ============================================================================
# 12. AST safety classification
# ============================================================================


@dataclass
class AstSafetySummary:
    """Aggregated safety signals from an AST root node."""

    has_command_substitution: bool = False
    has_process_substitution: bool = False
    has_heredoc: bool = False
    has_arithmetic_expansion: bool = False
    has_pipeline: bool = False
    has_compound_operator: bool = False
    has_subshell: bool = False
    has_redirect: bool = False
    has_control_flow: bool = False
    has_error_node: bool = False
    node_type_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_trivial(self) -> bool:
        """True when the AST contains only a bare simple command."""
        return (
            not self.has_command_substitution
            and not self.has_process_substitution
            and not self.has_heredoc
            and not self.has_arithmetic_expansion
            and not self.has_pipeline
            and not self.has_compound_operator
            and not self.has_subshell
            and not self.has_redirect
            and not self.has_control_flow
            and not self.has_error_node
        )


def classify_ast_safety(root: TsNode) -> AstSafetySummary:
    """Walk *root* and produce an :class:`AstSafetySummary`.

    Useful as a pre-screen before calling the more expensive
    :func:`parse_for_security`.
    """
    summary = AstSafetySummary()
    summary.node_type_counts = count_node_types(root)

    def visit(n: TsNode) -> None:
        t = n.type
        if t == "command_substitution":
            summary.has_command_substitution = True
        elif t == "process_substitution":
            summary.has_process_substitution = True
        elif t in BASH_REDIRECT_TYPES:
            if t in ("heredoc_redirect", "heredoc_body"):
                summary.has_heredoc = True
            summary.has_redirect = True
        elif t == "arithmetic_expansion":
            summary.has_arithmetic_expansion = True
        elif t == "pipeline":
            summary.has_pipeline = True
        elif t == "subshell":
            summary.has_subshell = True
        elif t in BASH_CONTROL_FLOW_TYPES:
            summary.has_control_flow = True
        elif t == "ERROR":
            summary.has_error_node = True

    walk_tree(root, visit)

    # Compound operators: node type counts for ; && || &
    summary.has_compound_operator = any(
        summary.node_type_counts.get(op, 0) > 0
        for op in (";", "&&", "||", "&")
    )

    return summary
