"""Shared classifier helpers. Port of classifierShared.ts."""

from __future__ import annotations

from typing import Any


def normalize_tool_input_for_classifier(
    _tool: str, _input: dict[str, Any]
) -> dict[str, Any]:
    return dict(_input)
