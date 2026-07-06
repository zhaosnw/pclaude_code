"""Port of: src/tools/ToolSearchTool/prompt.ts"""

from __future__ import annotations

from typing import Any

TOOL_SEARCH_TOOL_NAME = "ToolSearch"

PROMPT_HEAD = """\
Fetches full schema definitions for deferred tools so they can be called.

"""

PROMPT_TAIL = """\
 Until fetched, only the name is known — there is no parameter schema, so the tool cannot be invoked. This tool takes a query, matches it against the deferred tool list, and returns the matched tools' complete JSONSchema definitions inside a <functions> block. Once a tool's schema appears in that result, it is callable exactly like any tool defined at the top of the prompt.

Result format: each matched tool appears as one <function>{"description": "...", "name": "...", "parameters": {...}}</function> line inside the <functions> block — the same encoding as the tool list at the top of this prompt.

Query forms:
- "select:Read,Edit,Grep" — fetch these exact tools by name
- "notebook jupyter" — keyword search, up to max_results best matches
- "+slack send" — require "slack" in the name, rank by remaining terms"""


def get_tool_location_hint() -> str:
    return "Deferred tools appear by name in <system-reminder> messages."


def is_deferred_tool(tool: Any) -> bool:
    if getattr(tool, "always_load", False):
        return False
    if getattr(tool, "is_mcp", False):
        return True
    if getattr(tool, "name", "") == TOOL_SEARCH_TOOL_NAME:
        return False
    return getattr(tool, "should_defer", False)


def format_deferred_tool_line(tool: Any) -> str:
    return getattr(tool, "name", "")


def get_prompt() -> str:
    return PROMPT_HEAD + get_tool_location_hint() + PROMPT_TAIL
