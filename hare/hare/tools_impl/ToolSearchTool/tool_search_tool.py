"""
ToolSearchTool – fetch deferred tool schemas.

Port of: src/tools/ToolSearchTool/ToolSearchTool.ts
"""

from __future__ import annotations
from typing import Any
from hare.tools_impl.ToolSearchTool.prompt import (
    TOOL_SEARCH_TOOL_NAME,
    is_deferred_tool,
)

TOOL_NAME = TOOL_SEARCH_TOOL_NAME


def input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query or select:ToolA,ToolB",
            },
            "max_results": {"type": "number", "description": "Max tools to return"},
        },
        "required": ["query"],
    }


def _score_tool(tool: Any, terms: list[str]) -> float:
    name = getattr(tool, "name", "").lower()
    desc = getattr(tool, "description", "").lower()
    score = 0.0
    for t in terms:
        if t in name:
            score += 2.0
        if t in desc:
            score += 1.0
    return score


async def call(
    query: str,
    max_results: int = 5,
    tools: list[Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    available = tools or []
    deferred = [t for t in available if is_deferred_tool(t)]
    if query.startswith("select:"):
        names = [n.strip() for n in query[7:].split(",")]
        matched = [t for t in deferred if getattr(t, "name", "") in names]
    else:
        terms = query.lower().split()
        scored = [(t, _score_tool(t, terms)) for t in deferred]
        scored.sort(key=lambda x: -x[1])
        matched = [t for t, s in scored[:max_results] if s > 0]
    schemas = []
    for t in matched:
        schemas.append(
            {
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", ""),
                "parameters": getattr(t, "input_schema", {}),
            }
        )
    return {"matched_tools": schemas, "count": len(schemas)}
