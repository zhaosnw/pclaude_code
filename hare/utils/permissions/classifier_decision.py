"""Tool allowlists for classifier fast paths. Port of classifierDecision.ts."""

from __future__ import annotations

SAFE_YOLO_ALLOWLISTED_TOOLS: frozenset[str] = frozenset(
    {
        "Read",
        "Grep",
        "Glob",
        "LSP",
        "ToolSearch",
        "ListMcpResourcesTool",
        "ReadMcpResourceTool",
        "TodoWrite",
        "TaskCreate",
        "TaskGet",
        "TaskUpdate",
        "TaskList",
        "TaskStop",
        "TaskOutput",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "TeamCreate",
        "TeamDelete",
        "Sleep",
        "SendMessage",
    }
)


def is_safe_yolo_allowlisted(tool_name: str) -> bool:
    return tool_name in SAFE_YOLO_ALLOWLISTED_TOOLS
