"""Streamlined stdout message transform (port of streamlinedTransform.ts)."""

from __future__ import annotations

from typing import Any, Callable

from hare.utils.messages import extract_text_content
from hare.utils.shell.shell_tool_utils import SHELL_TOOL_NAMES
from hare.utils.string_utils import capitalize

FILE_READ_TOOL_NAME = "Read"
FILE_EDIT_TOOL_NAME = "Edit"
FILE_WRITE_TOOL_NAME = "Write"
GREP_TOOL_NAME = "Grep"
GLOB_TOOL_NAME = "Glob"
WEB_SEARCH_TOOL_NAME = "WebSearch"
LSP_TOOL_NAME = "LSP"
LIST_MCP_RESOURCES_TOOL_NAME = "ListMcpResources"
NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"
TASK_STOP_TOOL_NAME = "TaskStop"

SEARCH_TOOLS = [GREP_TOOL_NAME, GLOB_TOOL_NAME, WEB_SEARCH_TOOL_NAME, LSP_TOOL_NAME]
READ_TOOLS = [FILE_READ_TOOL_NAME, LIST_MCP_RESOURCES_TOOL_NAME]
WRITE_TOOLS = [FILE_WRITE_TOOL_NAME, FILE_EDIT_TOOL_NAME, NOTEBOOK_EDIT_TOOL_NAME]
COMMAND_TOOLS = [*SHELL_TOOL_NAMES, "Tmux", TASK_STOP_TOOL_NAME]


def _categorize(tool_name: str) -> str:
    if any(tool_name.startswith(t) for t in SEARCH_TOOLS):
        return "searches"
    if any(tool_name.startswith(t) for t in READ_TOOLS):
        return "reads"
    if any(tool_name.startswith(t) for t in WRITE_TOOLS):
        return "writes"
    if any(tool_name.startswith(t) for t in COMMAND_TOOLS):
        return "commands"
    return "other"


def _empty_counts() -> dict[str, int]:
    return {"searches": 0, "reads": 0, "writes": 0, "commands": 0, "other": 0}


def _summary_text(counts: dict[str, int]) -> str | None:
    parts: list[str] = []
    if counts["searches"] > 0:
        n = counts["searches"]
        parts.append(f"searched {n} {'pattern' if n == 1 else 'patterns'}")
    if counts["reads"] > 0:
        n = counts["reads"]
        parts.append(f"read {n} {'file' if n == 1 else 'files'}")
    if counts["writes"] > 0:
        n = counts["writes"]
        parts.append(f"wrote {n} {'file' if n == 1 else 'files'}")
    if counts["commands"] > 0:
        n = counts["commands"]
        parts.append(f"ran {n} {'command' if n == 1 else 'commands'}")
    if counts["other"] > 0:
        n = counts["other"]
        parts.append(f"{n} other {'tool' if n == 1 else 'tools'}")
    if not parts:
        return None
    return capitalize(", ".join(parts))


def _accumulate(message: Any, counts: dict[str, int]) -> None:
    content = message.message.content
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            cat = _categorize(str(block.get("name", "")))
            counts[cat] += 1


def create_streamlined_transform() -> Callable[[Any], Any | None]:
    cumulative = _empty_counts()

    def transform(message: Any) -> Any | None:
        nonlocal cumulative
        mtype = getattr(message, "type", None)
        if mtype == "assistant":
            content = message.message.content
            text = (
                extract_text_content(content, "\n").strip()
                if isinstance(content, list)
                else ""
            )
            _accumulate(message, cumulative)
            if text:
                cumulative = _empty_counts()
                return {
                    "type": "streamlined_text",
                    "text": text,
                    "session_id": getattr(message, "session_id", None),
                    "uuid": getattr(message, "uuid", None),
                }
            summary = _summary_text(cumulative)
            if not summary:
                return None
            return {
                "type": "streamlined_tool_use_summary",
                "tool_summary": summary,
                "session_id": getattr(message, "session_id", None),
                "uuid": getattr(message, "uuid", None),
            }
        if mtype == "result":
            return message
        return None

    return transform


def should_include_in_streamlined(message: Any) -> bool:
    t = getattr(message, "type", None)
    return t in ("assistant", "result")
