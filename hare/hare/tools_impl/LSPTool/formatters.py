"""Format LSP responses for model consumption. Port of: src/tools/LSPTool/formatters.ts"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import unquote as _uri_decode

from hare.utils.string_utils import pluralize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SymbolKind enum -> human-readable name
# ---------------------------------------------------------------------------

_SYMBOL_KINDS: dict[int, str] = {
    1: "File",
    2: "Module",
    3: "Namespace",
    4: "Package",
    5: "Class",
    6: "Method",
    7: "Property",
    8: "Field",
    9: "Constructor",
    10: "Enum",
    11: "Interface",
    12: "Function",
    13: "Variable",
    14: "Constant",
    15: "String",
    16: "Number",
    17: "Boolean",
    18: "Array",
    19: "Object",
    20: "Key",
    21: "Null",
    22: "EnumMember",
    23: "Struct",
    24: "Event",
    25: "Operator",
    26: "TypeParameter",
}


def _symbol_kind_to_string(kind: int) -> str:
    return _SYMBOL_KINDS.get(kind, "Unknown")


# ---------------------------------------------------------------------------
# URI formatting
# ---------------------------------------------------------------------------


def _format_uri(uri: str | None, cwd: str | None = None) -> str:
    """Format a URI by converting it to a relative path when possible.

    Handles URI decoding gracefully; falls back to the raw path on decode
    failure.  Prefers a relative path only when it is shorter and does not
    start with ``../../``.
    """
    if not uri:
        logger.warning(
            "_format_uri called with undefined URI — "
            "indicates malformed LSP server response"
        )
        return "<unknown location>"

    # Strip file:// protocol
    file_path = re.sub(r"^file://", "", uri)

    # On Windows, file:///C:/… becomes /C:/… after stripping; remove the
    # leading slash so os.path routines can handle it.
    if re.match(r"^/[A-Za-z]:", file_path):
        file_path = file_path[1:]

    # Decode percent-encoding
    try:
        file_path = _uri_decode(file_path)
    except Exception:
        logger.warning(
            "Failed to decode LSP URI %r. Using raw path: %s", uri, file_path
        )

    # Normalise to forward slashes for consistent display
    file_path = file_path.replace("\\", "/")

    if cwd:
        try:
            rel = os.path.relpath(file_path, cwd).replace("\\", "/")
        except ValueError:
            rel = file_path
        if len(rel) < len(file_path) and not rel.startswith("../../"):
            return rel

    return file_path


# ---------------------------------------------------------------------------
# Grouping helper
# ---------------------------------------------------------------------------


def _group_by_file(
    items: list[dict[str, Any]], cwd: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Group items by their file URI.

    Works with both ``{"uri": …}`` and ``{"location": {"uri": …}}`` shapes.
    """
    by_file: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        uri = item.get("uri") or item.get("location", {}).get("uri", "")
        file_path = _format_uri(uri, cwd)
        by_file.setdefault(file_path, []).append(item)
    return by_file


# ---------------------------------------------------------------------------
# Location / LocationLink helpers
# ---------------------------------------------------------------------------


def _format_location(loc: dict[str, Any], cwd: str | None = None) -> str:
    """Format a ``Location`` dict as ``<path>:<line>:<character>``."""
    file_path = _format_uri(loc.get("uri"), cwd)
    rng = loc.get("range", {})
    start = rng.get("start", {})
    line = start.get("line", 0) + 1  # 0-based → 1-based
    character = start.get("character", 0) + 1
    return f"{file_path}:{line}:{character}"


def _location_link_to_location(link: dict[str, Any]) -> dict[str, Any]:
    """Convert a ``LocationLink`` dict into the ``Location`` shape."""
    return {
        "uri": link.get("targetUri", ""),
        "range": link.get("targetSelectionRange") or link.get("targetRange", {}),
    }


def _is_location_link(item: dict[str, Any]) -> bool:
    return "targetUri" in item


# ---------------------------------------------------------------------------
# Extracting markup text (Hover contents)
# ---------------------------------------------------------------------------


def _extract_markup_text(
    contents: str | dict[str, Any] | list[Any],
) -> str:
    """Extract plain text from ``MarkupContent``, ``MarkedString``, or a
    list of either."""
    if isinstance(contents, list):
        parts: list[str] = []
        for item in contents:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("value", "")))
        return "\n\n".join(parts)

    if isinstance(contents, str):
        return contents

    # MarkupContent (has "kind") or MarkedString dict (has "value")
    return str(contents.get("value", ""))


# ===================================================================
# Public formatting functions
# ===================================================================


def format_go_to_definition_result(
    result: dict[str, Any] | list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format a *goToDefinition* response.

    Accepts a single ``Location`` / ``LocationLink``, a list of either, or
    ``None``.
    """
    if not result:
        return (
            "No definition found. This may occur if the cursor is not on a "
            "symbol, or if the definition is in an external library not "
            "indexed by the LSP server."
        )

    if isinstance(result, list):
        locations: list[dict[str, Any]] = []
        for item in result:
            locations.append(
                _location_link_to_location(item) if _is_location_link(item) else item
            )

        valid = [loc for loc in locations if loc and loc.get("uri")]
        if len(valid) == 0:
            return (
                "No definition found. This may occur if the cursor is not on a "
                "symbol, or if the definition is in an external library not "
                "indexed by the LSP server."
            )
        if len(valid) == 1:
            return f"Defined in {_format_location(valid[0], cwd)}"

        location_lines = "\n".join(
            f"  {_format_location(loc, cwd)}" for loc in valid
        )
        return f"Found {len(valid)} definitions:\n{location_lines}"

    # Single result
    loc = (
        _location_link_to_location(result)
        if _is_location_link(result)
        else result
    )
    return f"Defined in {_format_location(loc, cwd)}"


def format_find_references_result(
    result: list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format a *findReferences* response (list of ``Location`` dicts)."""
    if not result:
        return (
            "No references found. This may occur if the symbol has no usages, "
            "or if the LSP server has not fully indexed the workspace."
        )

    valid = [loc for loc in result if loc and loc.get("uri")]
    if len(valid) == 0:
        return (
            "No references found. This may occur if the symbol has no usages, "
            "or if the LSP server has not fully indexed the workspace."
        )

    if len(valid) == 1:
        return f"Found 1 reference:\n  {_format_location(valid[0], cwd)}"

    by_file = _group_by_file(valid, cwd)
    lines: list[str] = [
        f"Found {len(valid)} references across {len(by_file)} files:"
    ]
    for file_path, locs in by_file.items():
        lines.append(f"\n{file_path}:")
        for loc in locs:
            rng = loc.get("range", {})
            start = rng.get("start", {})
            line = start.get("line", 0) + 1
            character = start.get("character", 0) + 1
            lines.append(f"  Line {line}:{character}")

    return "\n".join(lines)


def format_hover_result(
    result: dict[str, Any] | None,
    _cwd: str | None = None,
) -> str:
    """Format a *hover* response."""
    if not result:
        return (
            "No hover information available. This may occur if the cursor is "
            "not on a symbol, or if the LSP server has not fully indexed the "
            "file."
        )

    content = _extract_markup_text(result.get("contents", ""))

    hover_range = result.get("range")
    if hover_range:
        start = hover_range.get("start", {})
        line = start.get("line", 0) + 1
        character = start.get("character", 0) + 1
        return f"Hover info at {line}:{character}:\n\n{content}"

    return content


def _format_document_symbol_node(
    symbol: dict[str, Any], indent: int = 0
) -> list[str]:
    """Recursively format a ``DocumentSymbol`` node."""
    lines: list[str] = []
    prefix = "  " * indent
    kind = _symbol_kind_to_string(symbol.get("kind", 0))

    line = f"{prefix}{symbol.get('name', '')} ({kind})"
    detail = symbol.get("detail")
    if detail:
        line += f" {detail}"

    symbol_range = symbol.get("range", {})
    symbol_start = symbol_range.get("start", {})
    symbol_line = symbol_start.get("line", 0) + 1
    line += f" - Line {symbol_line}"
    lines.append(line)

    children = symbol.get("children") or []
    for child in children:
        lines.extend(_format_document_symbol_node(child, indent + 1))

    return lines


def format_document_symbol_result(
    result: list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format a *documentSymbol* response.

    Handles both ``DocumentSymbol[]`` (hierarchical) and
    ``SymbolInformation[]`` (flat) per the LSP spec.
    """
    if not result:
        return (
            "No symbols found in document. This may occur if the file is "
            "empty, not supported by the LSP server, or if the server has "
            "not fully indexed the file."
        )

    first = result[0]
    if first and "location" in first:
        # SymbolInformation[] — delegate to workspace formatter
        return format_workspace_symbol_result(result, cwd)

    # DocumentSymbol[] (hierarchical)
    lines: list[str] = ["Document symbols:"]
    for symbol in result:
        lines.extend(_format_document_symbol_node(symbol))

    return "\n".join(lines)


def format_workspace_symbol_result(
    result: list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format a *workspaceSymbol* response (flat ``SymbolInformation[]``)."""
    if not result:
        return (
            "No symbols found in workspace. This may occur if the workspace "
            "is empty, or if the LSP server has not finished indexing the "
            "project."
        )

    valid = [
        sym
        for sym in result
        if sym and sym.get("location") and sym.get("location", {}).get("uri")
    ]
    if len(valid) == 0:
        return (
            "No symbols found in workspace. This may occur if the workspace "
            "is empty, or if the LSP server has not finished indexing the "
            "project."
        )

    lines: list[str] = [
        f"Found {len(valid)} {pluralize(len(valid), 'symbol')} in workspace:"
    ]

    by_file = _group_by_file(valid, cwd)
    for file_path, symbols in by_file.items():
        lines.append(f"\n{file_path}:")
        for sym in symbols:
            kind = _symbol_kind_to_string(sym.get("kind", 0))
            loc = sym.get("location", {})
            loc_start = loc.get("range", {}).get("start", {})
            line = loc_start.get("line", 0) + 1

            symbol_line = f"  {sym.get('name', '')} ({kind}) - Line {line}"
            container = sym.get("containerName")
            if container:
                symbol_line += f" in {container}"

            lines.append(symbol_line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Call-hierarchy helpers
# ---------------------------------------------------------------------------


def _format_call_hierarchy_item(
    item: dict[str, Any], cwd: str | None = None
) -> str:
    """Format a single ``CallHierarchyItem`` with name, kind, and location."""
    uri = item.get("uri")
    if not uri:
        logger.warning(
            "_format_call_hierarchy_item: CallHierarchyItem has undefined URI"
        )
        return (
            f"{item.get('name', '')} "
            f"({_symbol_kind_to_string(item.get('kind', 0))}) "
            f"- <unknown location>"
        )

    file_path = _format_uri(uri, cwd)
    line = item.get("range", {}).get("start", {}).get("line", 0) + 1
    kind = _symbol_kind_to_string(item.get("kind", 0))
    out = f"{item.get('name', '')} ({kind}) - {file_path}:{line}"
    detail = item.get("detail")
    if detail:
        out += f" [{detail}]"
    return out


def format_prepare_call_hierarchy_result(
    result: list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format a *prepareCallHierarchy* response."""
    if not result:
        return "No call hierarchy item found at this position"

    if len(result) == 1:
        return f"Call hierarchy item: {_format_call_hierarchy_item(result[0], cwd)}"

    lines = [f"Found {len(result)} call hierarchy items:"]
    for item in result:
        lines.append(f"  {_format_call_hierarchy_item(item, cwd)}")
    return "\n".join(lines)


def format_incoming_calls_result(
    result: list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format an *incomingCalls* response.

    Shows all functions / methods that call the target.
    """
    if not result:
        return "No incoming calls found (nothing calls this function)"

    lines = [
        f"Found {len(result)} incoming {pluralize(len(result), 'call')}:"
    ]

    by_file: dict[str, list[dict[str, Any]]] = {}
    for call in result:
        caller = call.get("from")
        if not caller:
            logger.warning(
                "format_incoming_calls_result: incoming call has undefined "
                "'from' field"
            )
            continue
        file_path = _format_uri(caller.get("uri"), cwd)
        by_file.setdefault(file_path, []).append(call)

    for file_path, calls in by_file.items():
        lines.append(f"\n{file_path}:")
        for call in calls:
            caller = call.get("from")
            if not caller:
                continue
            kind = _symbol_kind_to_string(caller.get("kind", 0))
            line = caller.get("range", {}).get("start", {}).get("line", 0) + 1
            call_line = f"  {caller.get('name', '')} ({kind}) - Line {line}"

            from_ranges = call.get("fromRanges") or []
            if from_ranges:
                sites = ", ".join(
                    f"{r.get('start', {}).get('line', 0) + 1}:"
                    f"{r.get('start', {}).get('character', 0) + 1}"
                    for r in from_ranges
                )
                call_line += f" [calls at: {sites}]"

            lines.append(call_line)

    return "\n".join(lines)


def format_outgoing_calls_result(
    result: list[dict[str, Any]] | None,
    cwd: str | None = None,
) -> str:
    """Format an *outgoingCalls* response.

    Shows all functions / methods called by the target.
    """
    if not result:
        return "No outgoing calls found (this function calls nothing)"

    lines = [
        f"Found {len(result)} outgoing {pluralize(len(result), 'call')}:"
    ]

    by_file: dict[str, list[dict[str, Any]]] = {}
    for call in result:
        callee = call.get("to")
        if not callee:
            logger.warning(
                "format_outgoing_calls_result: outgoing call has undefined "
                "'to' field"
            )
            continue
        file_path = _format_uri(callee.get("uri"), cwd)
        by_file.setdefault(file_path, []).append(call)

    for file_path, calls in by_file.items():
        lines.append(f"\n{file_path}:")
        for call in calls:
            callee = call.get("to")
            if not callee:
                continue
            kind = _symbol_kind_to_string(callee.get("kind", 0))
            line = callee.get("range", {}).get("start", {}).get("line", 0) + 1
            call_line = f"  {callee.get('name', '')} ({kind}) - Line {line}"

            from_ranges = call.get("fromRanges") or []
            if from_ranges:
                sites = ", ".join(
                    f"{r.get('start', {}).get('line', 0) + 1}:"
                    f"{r.get('start', {}).get('character', 0) + 1}"
                    for r in from_ranges
                )
                call_line += f" [called from: {sites}]"

            lines.append(call_line)

    return "\n".join(lines)
