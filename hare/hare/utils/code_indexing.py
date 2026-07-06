"""Detect code-indexing tools in commands and MCP (`codeIndexing.ts`)."""

from __future__ import annotations

import re
from typing import Literal

CodeIndexingTool = Literal[
    "sourcegraph",
    "hound",
    "seagoat",
    "bloop",
    "gitloop",
    "cody",
    "aider",
    "continue",
    "github-copilot",
    "cursor",
    "tabby",
    "codeium",
    "tabnine",
    "augment",
    "windsurf",
    "aide",
    "pieces",
    "qodo",
    "amazon-q",
    "gemini",
    "hare-context",
    "code-index-mcp",
    "local-code-search",
    "autodev-codebase",
    "openctx",
]

CLI_COMMAND_MAPPING: dict[str, str] = {
    "src": "sourcegraph",
    "cody": "cody",
    "aider": "aider",
    "tabby": "tabby",
    "tabnine": "tabnine",
    "augment": "augment",
    "pieces": "pieces",
    "qodo": "qodo",
    "aide": "aide",
    "hound": "hound",
    "seagoat": "seagoat",
    "bloop": "bloop",
    "gitloop": "gitloop",
    "q": "amazon-q",
    "gemini": "gemini",
}

MCP_SERVER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^sourcegraph$", re.I), "sourcegraph"),
    (re.compile(r"^cody$", re.I), "cody"),
    (re.compile(r"^openctx$", re.I), "openctx"),
    (re.compile(r"^aider$", re.I), "aider"),
    (re.compile(r"^continue$", re.I), "continue"),
    (re.compile(r"^github[-_]?copilot$", re.I), "github-copilot"),
    (re.compile(r"^copilot$", re.I), "github-copilot"),
    (re.compile(r"^cursor$", re.I), "cursor"),
    (re.compile(r"^tabby$", re.I), "tabby"),
    (re.compile(r"^codeium$", re.I), "codeium"),
    (re.compile(r"^tabnine$", re.I), "tabnine"),
    (re.compile(r"^augment[-_]?code$", re.I), "augment"),
    (re.compile(r"^augment$", re.I), "augment"),
    (re.compile(r"^windsurf$", re.I), "windsurf"),
    (re.compile(r"^aide$", re.I), "aide"),
    (re.compile(r"^codestory$", re.I), "aide"),
    (re.compile(r"^pieces$", re.I), "pieces"),
    (re.compile(r"^qodo$", re.I), "qodo"),
    (re.compile(r"^amazon[-_]?q$", re.I), "amazon-q"),
    (re.compile(r"^gemini[-_]?code[-_]?assist$", re.I), "gemini"),
    (re.compile(r"^gemini$", re.I), "gemini"),
    (re.compile(r"^hound$", re.I), "hound"),
    (re.compile(r"^seagoat$", re.I), "seagoat"),
    (re.compile(r"^bloop$", re.I), "bloop"),
    (re.compile(r"^gitloop$", re.I), "gitloop"),
    (re.compile(r"^hare[-_]?context$", re.I), "hare-context"),
    (re.compile(r"^code[-_]?index[-_]?mcp$", re.I), "code-index-mcp"),
    (re.compile(r"^code[-_]?index$", re.I), "code-index-mcp"),
    (re.compile(r"^local[-_]?code[-_]?search$", re.I), "local-code-search"),
    (re.compile(r"^codebase$", re.I), "autodev-codebase"),
    (re.compile(r"^autodev[-_]?codebase$", re.I), "autodev-codebase"),
    (re.compile(r"^code[-_]?context$", re.I), "hare-context"),
]


def detect_code_indexing_from_command(command: str) -> str | None:
    trimmed = command.strip()
    parts = trimmed.split()
    if not parts:
        return None
    first = parts[0].lower()
    if first in ("npx", "bunx") and len(parts) > 1:
        second = parts[1].lower()
        return CLI_COMMAND_MAPPING.get(second)
    return CLI_COMMAND_MAPPING.get(first)


def detect_code_indexing_from_mcp_tool(tool_name: str) -> str | None:
    if not tool_name.startswith("mcp__"):
        return None
    segments = tool_name.split("__")
    if len(segments) < 3:
        return None
    server_name = segments[1]
    if not server_name:
        return None
    return detect_code_indexing_from_mcp_server_name(server_name)


def detect_code_indexing_from_mcp_server_name(server_name: str) -> str | None:
    for pattern, tool in MCP_SERVER_PATTERNS:
        if pattern.match(server_name):
            return tool
    return None
