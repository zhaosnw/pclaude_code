"""Resolve symbol context for LSP tool queries. Port of: src/tools/LSPTool/symbolContext.ts

Given a file position and LSP document-symbol / hover / definition results,
this module extracts the enclosing symbol, its kind, signature, and
container hierarchy so that downstream consumers (prompt formatters,
call-hierarchy builders) can present rich context to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# LSP protocol position / range helpers
# ---------------------------------------------------------------------------


def _lsp_pos_after(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True when position *a* is strictly after position *b*."""
    if a["line"] != b["line"]:
        return a["line"] > b["line"]
    return a["character"] > b["character"]


def _lsp_pos_before_or_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True when position *a* is before or equal to position *b*."""
    if a["line"] != b["line"]:
        return a["line"] < b["line"]
    return a["character"] <= b["character"]


def _position_in_range(
    pos: dict[str, Any], rng: dict[str, Any]
) -> bool:
    """Check whether *pos* falls inside *rng* (inclusive of start, exclusive of end)."""
    start_ok = _lsp_pos_before_or_equal(rng["start"], pos)
    end_ok = _lsp_pos_after(rng["end"], pos)
    return start_ok and end_ok


# ---------------------------------------------------------------------------
# Symbol kind constants (subset of LSP SymbolKind)
# ---------------------------------------------------------------------------

SYMBOL_KIND_NAMES: dict[int, str] = {
    1:  "file",
    2:  "module",
    3:  "namespace",
    4:  "package",
    5:  "class",
    6:  "method",
    7:  "property",
    8:  "field",
    9:  "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type parameter",
}

SYMBOL_CONTAINER_KINDS: frozenset[int] = frozenset({2, 3, 4, 5, 10, 11, 23})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SymbolContext:
    """Rich symbol information at a given document position."""

    uri: str
    line: int
    character: int
    name: str = ""
    kind: str = ""
    kind_id: int = 0
    signature: str = ""
    documentation: str = ""
    container_name: str = ""
    definition_uri: str = ""
    definition_line: int = 0
    definition_character: int = 0
    enclosing_symbols: list[SymbolContext] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        """Build `ParentClass.methodName` style qualified name."""
        if self.container_name:
            return f"{self.container_name}.{self.name}"
        return self.name


@dataclass
class ResolvedLocation:
    """A single resolved location (file + range) for a symbol."""

    uri: str
    start_line: int
    start_character: int
    end_line: int
    end_character: int


# ---------------------------------------------------------------------------
# Recursive document-symbol tree traversal
# ---------------------------------------------------------------------------


def find_enclosing_symbol(
    symbols: list[dict[str, Any]],
    line: int,
    character: int,
    *,
    parents: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Walk a *DocumentSymbol[]* tree to find the innermost symbol at *line:character*.

    Returns the deepest symbol whose range contains the position, or ``None``.
    """
    if parents is None:
        parents = []

    target_pos = {"line": line, "character": character}
    best: dict[str, Any] | None = None
    best_depth = -1

    def _walk(syms: list[dict[str, Any]], depth: int) -> None:
        nonlocal best, best_depth
        for sym in syms:
            rng = sym.get("range") or sym.get("selectionRange")
            if not rng:
                continue
            if _position_in_range(target_pos, rng):
                if depth > best_depth:
                    best = sym
                    best_depth = depth
                children = sym.get("children")
                if isinstance(children, list) and children:
                    _walk(children, depth + 1)

    _walk(symbols, 0)
    return best


def collect_symbol_hierarchy(
    symbols: list[dict[str, Any]],
    line: int,
    character: int,
) -> list[dict[str, Any]]:
    """Return every ancestor symbol (outermost first) that contains *line:character*."""
    target_pos = {"line": line, "character": character}
    chain: list[dict[str, Any]] = []

    def _walk(syms: list[dict[str, Any]], ancestors: list[dict[str, Any]]) -> None:
        for sym in syms:
            rng = sym.get("range") or sym.get("selectionRange")
            if not rng:
                continue
            if _position_in_range(target_pos, rng):
                path = ancestors + [sym]
                children = sym.get("children")
                if isinstance(children, list) and children:
                    _walk(children, path)
                else:
                    chain.extend(path)

    _walk(symbols, [])
    return chain


def _symbol_to_context(
    sym: dict[str, Any],
    uri: str,
    *,
    container_name: str = "",
) -> SymbolContext:
    """Convert a raw LSP symbol dict into a *SymbolContext*."""
    kind_id = sym.get("kind", 0)
    rng = sym.get("range") or sym.get("selectionRange") or {}
    start = rng.get("start", {})
    return SymbolContext(
        uri=uri,
        line=start.get("line", 0),
        character=start.get("character", 0),
        name=sym.get("name", ""),
        kind=SYMBOL_KIND_NAMES.get(kind_id, "unknown"),
        kind_id=kind_id,
        container_name=sym.get("containerName", container_name),
    )


# ---------------------------------------------------------------------------
# Public API: build context from LSP results
# ---------------------------------------------------------------------------


async def build_symbol_context(
    uri: str,
    line: int,
    character: int,
    *,
    document_symbols: list[dict[str, Any]] | None = None,
    hover_result: dict[str, Any] | None = None,
    definition_result: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> SymbolContext | None:
    """Build a *SymbolContext* by combining document-symbol, hover, and definition data.

    Parameters
    ----------
    uri:
        The document URI.
    line / character:
        The cursor position (0-based LSP coordinates).
    document_symbols:
        Raw ``textDocument/documentSymbol`` response (``DocumentSymbol[]``).
    hover_result:
        Raw ``textDocument/hover`` response.
    definition_result:
        Raw ``textDocument/definition`` response (single or list of ``Location``).

    Returns
    -------
    SymbolContext | None
        Populated context, or ``None`` when no symbol can be resolved.
    """
    # ---- Step 1: find enclosing symbol from document symbols -----------------
    enclosing_sym: dict[str, Any] | None = None
    hierarchy: list[dict[str, Any]] = []

    if document_symbols:
        enclosing_sym = find_enclosing_symbol(document_symbols, line, character)
        if enclosing_sym:
            hierarchy = collect_symbol_hierarchy(document_symbols, line, character)
            # Remove the leaf from hierarchy to get only ancestors
            if len(hierarchy) > 1:
                hierarchy = hierarchy[:-1]

    if enclosing_sym is None:
        return None

    container = enclosing_sym.get("containerName", "")
    if not container and hierarchy:
        # Derive container name from the immediate parent in the hierarchy
        container = hierarchy[-1].get("name", "")

    ctx = _symbol_to_context(enclosing_sym, uri, container_name=container)

    # ---- Step 1b: collect enclosing (ancestor) symbol contexts --------------
    ctx.enclosing_symbols = [
        _symbol_to_context(a, uri)
        for a in hierarchy
    ]

    # ---- Step 2: enrich from hover result -----------------------------------
    if hover_result and isinstance(hover_result, dict):
        contents = hover_result.get("contents")
        if isinstance(contents, dict):
            ctx.signature = _extract_signature(contents)
            ctx.documentation = _extract_documentation(contents)
        elif isinstance(contents, str):
            ctx.signature = contents
        elif isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("value", ""))
            ctx.signature = "\n".join(parts)

    # ---- Step 3: enrich from definition result ------------------------------
    loc = _resolve_first_location(definition_result)
    if loc:
        ctx.definition_uri = loc.uri
        ctx.definition_line = loc.start_line
        ctx.definition_character = loc.start_character

    return ctx


async def build_symbol_context_from_raw(
    uri: str,
    line: int,
    character: int,
    raw_lsp_responses: dict[str, Any],
) -> SymbolContext | None:
    """Convenience wrapper that pulls known keys from a composite LSP response dict.

    *raw_lsp_responses* may contain keys ``documentSymbols``, ``hover``,
    ``definition`` matching the corresponding LSP method results.
    """
    return await build_symbol_context(
        uri=uri,
        line=line,
        character=character,
        document_symbols=raw_lsp_responses.get("documentSymbols"),
        hover_result=raw_lsp_responses.get("hover"),
        definition_result=raw_lsp_responses.get("definition"),
    )


def resolve_location_from_definition(
    definition_result: dict[str, Any] | list[dict[str, Any]] | None,
) -> ResolvedLocation | None:
    """Extract the first *ResolvedLocation* from a ``textDocument/definition`` result."""
    return _resolve_first_location(definition_result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_first_location(
    raw: dict[str, Any] | list[dict[str, Any]] | None,
) -> ResolvedLocation | None:
    """Normalise the polymorphic LSP definition result into a single location."""
    if raw is None:
        return None
    if isinstance(raw, list):
        if not raw:
            return None
        raw = raw[0]
    if not isinstance(raw, dict):
        return None
    uri = raw.get("uri", "")
    rng = raw.get("range") or raw.get("selectionRange") or {}
    start = rng.get("start", {})
    end = rng.get("end", {})
    if not uri:
        return None
    return ResolvedLocation(
        uri=uri,
        start_line=start.get("line", 0),
        start_character=start.get("character", 0),
        end_line=end.get("line", 0),
        end_character=end.get("character", 0),
    )


def _extract_signature(contents: dict[str, Any]) -> str:
    """Pull a human-readable signature from a hover *MarkupContent* or *MarkedString*."""
    # MarkupContent (LSP 3.16+)
    if "value" in contents:
        return _collapse_markdown(contents["value"])
    # Language-tagged MarkedString: {"language": "python", "value": "def foo(...)"}
    if "language" in contents and "value" in contents:
        return contents["value"]
    return ""


def _extract_documentation(contents: dict[str, Any]) -> str:
    """Pull documentation text from a hover result."""
    doc = contents.get("documentation")
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        return doc.get("value", "")
    return ""


def _collapse_markdown(text: str) -> str:
    """Strip markdown code fences and leading/trailing whitespace from a signature."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening fence (may have language tag)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text
