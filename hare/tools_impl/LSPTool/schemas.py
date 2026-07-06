"""JSON schemas for LSPTool inputs with validation. Port of: src/tools/LSPTool/schemas.ts

Provides discriminated-union JSON Schema for 9 LSP operations (goToDefinition,
findReferences, hover, documentSymbol, workspaceSymbol, goToImplementation,
prepareCallHierarchy, incomingCalls, outgoingCalls), plus input validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# LSP operation constants
# ---------------------------------------------------------------------------

LSP_OPERATIONS: tuple[str, ...] = (
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
)

LSP_OPERATIONS_SET: frozenset[str] = frozenset(LSP_OPERATIONS)

# ---------------------------------------------------------------------------
# Structured types
# ---------------------------------------------------------------------------


@dataclass
class LSPPosition:
    """1-based line and character position in a source file."""

    line: int
    character: int

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not isinstance(self.line, int) or self.line < 1:
            errors.append(f"line must be a positive integer, got {self.line!r}")
        if not isinstance(self.character, int) or self.character < 1:
            errors.append(f"character must be a positive integer, got {self.character!r}")
        return errors


@dataclass
class LSPInput:
    """Validated LSP tool input."""

    operation: str
    file_path: str
    position: LSPPosition


# ---------------------------------------------------------------------------
# Base property schemas (shared across operations)
# ---------------------------------------------------------------------------

def _file_path_prop() -> dict[str, Any]:
    return {"type": "string", "description": "The absolute or relative path to the file"}


def _line_prop() -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 1,
        "description": "The line number (1-based, as shown in editors)",
    }


def _character_prop() -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": 1,
        "description": "The character offset (1-based, as shown in editors)",
    }


def _operation_enum() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": list(LSP_OPERATIONS),
        "description": "The LSP operation to perform",
    }


# ---------------------------------------------------------------------------
# Per-operation schemas (standard JSON Schema objects)
# ---------------------------------------------------------------------------

_OPERATION_SCHEMAS: dict[str, dict[str, Any]] = {}


def _register(op: str, **extra_props: Any) -> None:
    props: dict[str, Any] = {
        "operation": {**_operation_enum(), "const": op},
        "filePath": _file_path_prop(),
    }
    props.update(extra_props)
    _OPERATION_SCHEMAS[op] = {
        "type": "object",
        "properties": props,
        "required": ["operation", "filePath"],
        "additionalProperties": False,
    }


for _op in LSP_OPERATIONS:
    _register(_op, line=_line_prop(), character=_character_prop())

# ---------------------------------------------------------------------------
# Discriminated union schema (main export)
# ---------------------------------------------------------------------------


def lsp_tool_input_schema() -> dict[str, Any]:
    """Return the discriminated-union JSON Schema for all LSP operations.

    Uses ``operation`` as the discriminator.  Compatible with the existing
    call signature so downstream callers are not broken.
    """
    return {
        "type": "object",
        "properties": {
            "operation": _operation_enum(),
            "filePath": _file_path_prop(),
            "line": _line_prop(),
            "character": _character_prop(),
        },
        "required": ["operation", "filePath"],
        "additionalProperties": False,
        "oneOf": [_make_one_of_entry(op, s) for op, s in _OPERATION_SCHEMAS.items()],
    }


def _make_one_of_entry(op: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": schema["properties"],
        "required": schema["required"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def is_valid_lsp_operation(operation: str) -> bool:
    """Type guard: return True if *operation* names a known LSP operation."""
    return operation in LSP_OPERATIONS_SET


def get_operation_schema(operation: str) -> dict[str, Any] | None:
    """Return the JSON Schema for a single operation, or None if unknown."""
    return _OPERATION_SCHEMAS.get(operation)


def get_all_operations() -> tuple[str, ...]:
    """Return all valid LSP operation names in definition order."""
    return LSP_OPERATIONS


def validate_lsp_input(
    operation: str,
    file_path: str,
    line: int | float = 0,
    character: int | float = 0,
) -> tuple[LSPInput | None, list[str]]:
    """Validate LSP tool input and return (parsed, errors).

    Returns a tuple of ``(LSPInput, [])`` on success or ``(None, [messages])``
    on failure.
    """
    errors: list[str] = []

    if not isinstance(operation, str) or not operation.strip():
        errors.append("operation is required and must be a string")
        return None, errors

    operation = operation.strip()
    if not is_valid_lsp_operation(operation):
        errors.append(
            f"Unknown LSP operation {operation!r}. "
            f"Valid operations: {', '.join(LSP_OPERATIONS)}"
        )
        return None, errors

    if not isinstance(file_path, str) or not file_path.strip():
        errors.append("filePath is required and must be a non-empty string")
        return None, errors

    file_path = file_path.strip()

    position = LSPPosition(line=int(line), character=int(character))
    pos_errors = position.validate()
    if pos_errors:
        errors.extend(pos_errors)
        return None, errors

    return LSPInput(operation=operation, file_path=file_path, position=position), []


def get_required_params(operation: str) -> list[str] | None:
    """Return required parameter names for *operation*, or None if unknown."""
    schema = get_operation_schema(operation)
    if schema is None:
        return None
    return list(schema.get("required", []))
