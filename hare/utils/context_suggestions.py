"""Context usage suggestions (`contextSuggestions.ts`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from hare.utils.format import format_tokens

BASH_TOOL_NAME = "Bash"
FILE_READ_TOOL_NAME = "Read"
GREP_TOOL_NAME = "Grep"
WEB_FETCH_TOOL_NAME = "WebFetch"

SuggestionSeverity = Literal["info", "warning"]


@dataclass
class ContextSuggestion:
    severity: SuggestionSeverity
    title: str
    detail: str
    savings_tokens: int | None = None


class ContextData(Protocol):
    percentage: int
    raw_max_tokens: int
    is_auto_compact_enabled: bool
    message_breakdown: "MessageBreakdown | None"
    memory_files: list["MemoryFileInfo"]


class MessageBreakdown(Protocol):
    tool_calls_by_type: list["ToolCallAgg"]


class ToolCallAgg(Protocol):
    name: str
    call_tokens: int
    result_tokens: int


class MemoryFileInfo(Protocol):
    path: str
    tokens: int


LARGE_TOOL_RESULT_PERCENT = 15
LARGE_TOOL_RESULT_TOKENS = 10_000
READ_BLOAT_PERCENT = 5
NEAR_CAPACITY_PERCENT = 80
MEMORY_HIGH_PERCENT = 5
MEMORY_HIGH_TOKENS = 5_000


def generate_context_suggestions(data: ContextData) -> list[ContextSuggestion]:
    suggestions: list[ContextSuggestion] = []
    _check_near_capacity(data, suggestions)
    _check_large_tool_results(data, suggestions)
    _check_read_result_bloat(data, suggestions)
    _check_memory_bloat(data, suggestions)
    _check_auto_compact_disabled(data, suggestions)

    suggestions.sort(
        key=lambda a: (
            0 if a.severity == "warning" else 1,
            -(a.savings_tokens or 0),
        )
    )
    return suggestions


def _check_near_capacity(
    data: ContextData, suggestions: list[ContextSuggestion]
) -> None:
    if data.percentage >= NEAR_CAPACITY_PERCENT:
        suggestions.append(
            ContextSuggestion(
                severity="warning",
                title=f"Context is {data.percentage}% full",
                detail=(
                    "Autocompact will trigger soon, which discards older messages. Use /compact now to control what gets kept."
                    if data.is_auto_compact_enabled
                    else "Autocompact is disabled. Use /compact to free space, or enable autocompact in /config."
                ),
            )
        )


def _get_large_tool_suggestion(
    tool_name: str,
    tokens: int,
    percent: float,
) -> ContextSuggestion | None:
    token_str = format_tokens(tokens)
    if tool_name == BASH_TOOL_NAME:
        return ContextSuggestion(
            severity="warning",
            title=f"Bash results using {token_str} tokens ({percent:.0f}%)",
            detail=(
                "Pipe output through head, tail, or grep to reduce result size. "
                "Avoid cat on large files — use Read with offset/limit instead."
            ),
            savings_tokens=int(tokens * 0.5),
        )
    if tool_name == FILE_READ_TOOL_NAME:
        return ContextSuggestion(
            severity="info",
            title=f"Read results using {token_str} tokens ({percent:.0f}%)",
            detail=(
                "Use offset and limit parameters to read only the sections you need. "
                "Avoid re-reading entire files when you only need a few lines."
            ),
            savings_tokens=int(tokens * 0.3),
        )
    if tool_name == GREP_TOOL_NAME:
        return ContextSuggestion(
            severity="info",
            title=f"Grep results using {token_str} tokens ({percent:.0f}%)",
            detail=(
                "Add more specific patterns or use the glob or type parameter to narrow file types. "
                "Consider Glob for file discovery instead of Grep."
            ),
            savings_tokens=int(tokens * 0.3),
        )
    if tool_name == WEB_FETCH_TOOL_NAME:
        return ContextSuggestion(
            severity="info",
            title=f"WebFetch results using {token_str} tokens ({percent:.0f}%)",
            detail="Web page content can be very large. Consider extracting only the specific information needed.",
            savings_tokens=int(tokens * 0.4),
        )
    if percent >= 20:
        return ContextSuggestion(
            severity="info",
            title=f"{tool_name} using {token_str} tokens ({percent:.0f}%)",
            detail="This tool is consuming a significant portion of context.",
            savings_tokens=int(tokens * 0.2),
        )
    return None


def _check_large_tool_results(
    data: ContextData, suggestions: list[ContextSuggestion]
) -> None:
    mb = data.message_breakdown
    if mb is None:
        return
    for tool in mb.tool_calls_by_type:
        total = tool.call_tokens + tool.result_tokens
        percent = (total / data.raw_max_tokens) * 100
        if percent < LARGE_TOOL_RESULT_PERCENT or total < LARGE_TOOL_RESULT_TOKENS:
            continue
        s = _get_large_tool_suggestion(tool.name, total, percent)
        if s:
            suggestions.append(s)


def _check_read_result_bloat(
    data: ContextData, suggestions: list[ContextSuggestion]
) -> None:
    mb = data.message_breakdown
    if mb is None:
        return
    read_tool = next(
        (t for t in mb.tool_calls_by_type if t.name == FILE_READ_TOOL_NAME), None
    )
    if read_tool is None:
        return
    total_read_tokens = read_tool.call_tokens + read_tool.result_tokens
    total_read_percent = (total_read_tokens / data.raw_max_tokens) * 100
    read_percent = (read_tool.result_tokens / data.raw_max_tokens) * 100
    if (
        total_read_percent >= LARGE_TOOL_RESULT_PERCENT
        and total_read_tokens >= LARGE_TOOL_RESULT_TOKENS
    ):
        return
    if (
        read_percent >= READ_BLOAT_PERCENT
        and read_tool.result_tokens >= LARGE_TOOL_RESULT_TOKENS
    ):
        suggestions.append(
            ContextSuggestion(
                severity="info",
                title=(
                    f"File reads using {format_tokens(read_tool.result_tokens)} tokens ({read_percent:.0f}%)"
                ),
                detail=(
                    "If you are re-reading files, consider referencing earlier reads. "
                    "Use offset/limit for large files."
                ),
                savings_tokens=int(read_tool.result_tokens * 0.3),
            )
        )


def _check_memory_bloat(
    data: ContextData, suggestions: list[ContextSuggestion]
) -> None:
    total_memory_tokens = sum(f.tokens for f in data.memory_files)
    memory_percent = (total_memory_tokens / data.raw_max_tokens) * 100
    if memory_percent < MEMORY_HIGH_PERCENT or total_memory_tokens < MEMORY_HIGH_TOKENS:
        return
    largest = sorted(data.memory_files, key=lambda f: f.tokens, reverse=True)[:3]
    parts = [f"{f.path} ({format_tokens(f.tokens)})" for f in largest]
    suggestions.append(
        ContextSuggestion(
            severity="info",
            title=(
                f"Memory files using {format_tokens(total_memory_tokens)} tokens ({memory_percent:.0f}%)"
            ),
            detail=f"Largest: {', '.join(parts)}. Use /memory to review and prune stale entries.",
            savings_tokens=int(total_memory_tokens * 0.3),
        )
    )


def _check_auto_compact_disabled(
    data: ContextData, suggestions: list[ContextSuggestion]
) -> None:
    if (
        not data.is_auto_compact_enabled
        and 50 <= data.percentage < NEAR_CAPACITY_PERCENT
    ):
        suggestions.append(
            ContextSuggestion(
                severity="info",
                title="Autocompact is disabled",
                detail=(
                    "Without autocompact, you will hit context limits and lose the conversation. "
                    "Enable it in /config or use /compact manually."
                ),
            )
        )
