"""Structured result for permission prompt tool. Port of PermissionPromptToolResultSchema.ts."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class PermissionPromptToolResult(TypedDict, total=False):
    decision: Literal["allow", "deny", "ask"]
    mode: str
    updated_rules: list[dict[str, Any]]
    message: str


def validate_permission_prompt_tool_result(
    data: dict[str, Any],
) -> PermissionPromptToolResult:
    """Minimal validation — extend with pydantic if needed."""
    return data  # type: ignore[return-value]
