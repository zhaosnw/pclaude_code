"""
LSPTool – Language Server Protocol operations.

Port of: src/tools/LSPTool/LSPTool.ts

Provides access to LSP features: go-to-definition, find references,
hover info, diagnostics, rename, document symbols, and call hierarchy.
"""

from __future__ import annotations
from typing import Any

TOOL_NAME = "LSP"

OPERATIONS = [
    "definition", "references", "hover", "diagnostics",
    "rename", "documentSymbols", "incomingCalls", "outgoingCalls",
]


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": OPERATIONS,
                "description": "The LSP operation to perform",
            },
            "filePath": {
                "type": "string",
                "description": "Absolute or relative file path",
            },
            "line": {
                "type": "integer",
                "description": "Line number (0-based for LSP, 1-based display)",
            },
            "character": {
                "type": "integer",
                "description": "Character/column offset (0-based for LSP)",
            },
            "newName": {
                "type": "string",
                "description": "New name for rename operation",
            },
        },
        "required": ["operation", "filePath"],
    }


def is_read_only(input: dict[str, Any]) -> bool:
    """Most LSP operations are read-only, but rename is destructive."""
    return input.get("operation", "") != "rename"


def is_concurrency_safe(input: dict[str, Any]) -> bool:
    return True


async def call(
    operation: str,
    filePath: str,
    line: int = 0,
    character: int = 0,
    newName: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute an LSP operation against the appropriate language server."""
    if operation not in OPERATIONS:
        return {
            "error": f"Unknown operation '{operation}'. Available: {', '.join(OPERATIONS)}",
            "data": "",
        }

    import os
    filepath = os.path.abspath(filePath) if not os.path.isabs(filePath) else filePath

    # Get the LSP manager from context or create one
    lsp_manager = kwargs.get("_lsp_manager") or kwargs.get("lsp_manager")
    if lsp_manager is None:
        try:
            from hare.services.lsp.lsp_server_manager import LSPServerManager
            lsp_manager = LSPServerManager()
        except ImportError:
            return {
                "error": "LSP server manager not available.",
                "data": "",
            }

    # Find the appropriate client for this file
    try:
        client = await lsp_manager.get_client_for_file(filepath)
    except Exception:
        client = None

    if client is None:
        return {
            "error": f"No LSP server found for file: {filepath}. Install a language server for this file type.",
            "data": "",
        }

    if not client.connected:
        return {
            "error": f"LSP server '{client.server_name}' is not connected.",
            "data": "",
        }

    # Convert 1-based line to 0-based for LSP protocol
    lsp_line = max(0, line - 1) if line > 0 else line
    lsp_col = character

    try:
        if operation == "definition":
            result = await client.get_definition(filepath, lsp_line, lsp_col)
            return _format_definition_result(result)

        elif operation == "references":
            refs = await client.get_references(filepath, lsp_line, lsp_col)
            return {
                "data": refs,
                "count": len(refs),
                "operation": "references",
                "filePath": filepath,
            }

        elif operation == "hover":
            text = await client.get_hover(filepath, lsp_line, lsp_col)
            return {
                "data": text or "No hover information available.",
                "operation": "hover",
                "filePath": filepath,
            }

        elif operation == "diagnostics":
            diags = await client.get_diagnostics(filepath)
            return {
                "data": diags,
                "count": len(diags),
                "operation": "diagnostics",
                "filePath": filepath,
            }

        elif operation == "rename":
            if not newName:
                return {"error": "newName is required for rename operation."}
            result = await client.rename(filepath, lsp_line, lsp_col, newName)
            return {
                "data": result,
                "operation": "rename",
                "filePath": filepath,
                "newName": newName,
            }

        elif operation == "documentSymbols":
            symbols = await client.get_document_symbols(filepath)
            return {
                "data": symbols,
                "count": len(symbols),
                "operation": "documentSymbols",
                "filePath": filepath,
            }

        elif operation in ("incomingCalls", "outgoingCalls"):
            return {
                "data": f"Call hierarchy ({operation}) — requires LSP 3.16+ server support.",
                "operation": operation,
                "filePath": filepath,
            }

        return {"data": f"Operation '{operation}' completed.", "filePath": filepath}

    except Exception as e:
        return {
            "error": f"LSP {operation} failed: {e}",
            "data": "",
            "filePath": filepath,
        }


def _format_definition_result(result: Any) -> dict[str, Any]:
    """Format a go-to-definition result for display."""
    if result is None:
        return {"data": "No definition found."}
    if isinstance(result, list):
        locations = []
        for loc in result:
            if isinstance(loc, dict):
                uri = loc.get("uri", "")
                rng = loc.get("range", {})
                start = rng.get("start", {})
                locations.append({
                    "uri": uri,
                    "line": start.get("line", 0) + 1,
                    "character": start.get("character", 0),
                })
        return {"data": locations, "count": len(locations)}
    if isinstance(result, dict):
        uri = result.get("uri", "")
        rng = result.get("range", {})
        start = rng.get("start", {})
        return {
            "data": [{
                "uri": uri,
                "line": start.get("line", 0) + 1,
                "character": start.get("character", 0),
            }],
            "count": 1,
        }
    return {"data": str(result)}
