"""Port of: src/utils/permissions/PermissionRule.ts + permissionRuleParser.ts

Handles parsing permission rule strings in the format "ToolName" or "ToolName(content)",
with proper handling of escaped parentheses and backslashes.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class PermissionRuleValue:
    tool_name: str
    rule_content: str = ""


# ---------------------------------------------------------------------------
# Escape / unescape (matching TS permissionRuleParser.ts L52-86)
# ---------------------------------------------------------------------------


def escape_rule_content(content: str) -> str:
    """Escape special chars in rule content for safe storage.

    TS escapeRuleContent: backslashes first, then parens.
    """
    return (
        content.replace("\\", "\\\\")  # escape backslashes first
        .replace("(", "\\(")  # escape opening parentheses
        .replace(")", "\\)")  # escape closing parentheses
    )


def unescape_rule_content(content: str) -> str:
    """Reverse the escaping done by escapeRuleContent.

    TS unescapeRuleContent: parens first (reverse order), then backslashes.
    """
    return (
        content.replace("\\)", ")")  # unescape closing parens
        .replace("\\(", "(")  # unescape opening parens
        .replace("\\\\", "\\")  # unescape backslashes last
    )


# ---------------------------------------------------------------------------
# Unescaped character search (matching TS findFirstUnescapedChar / findLastUnescapedChar)
# ---------------------------------------------------------------------------


def _find_first_unescaped_char(s: str, char: str) -> int:
    """Find index of first unescaped occurrence of char in s.

    A backslash before the character escapes it.
    """
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2  # skip escaped char
            continue
        if s[i] == char:
            return i
        i += 1
    return -1


def _find_last_unescaped_char(s: str, char: str) -> int:
    """Find index of last unescaped occurrence of char in s."""
    last = -1
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2  # skip escaped char
            continue
        if s[i] == char:
            last = i
        i += 1
    return last


# ---------------------------------------------------------------------------
# Main parser (matching TS permissionRuleValueFromString L93-133)
# ---------------------------------------------------------------------------


def parse_permission_rule(rule_string: str) -> PermissionRuleValue:
    """Parse a permission rule string into tool_name and optional rule_content.

    TS permissionRuleValueFromString:
    - "Bash" → {toolName: "Bash"}
    - "Bash(npm install)" → {toolName: "Bash", ruleContent: "npm install"}
    - "Bash(python -c \"print\\(1\\)\")" → {toolName: "Bash", ruleContent: 'python -c "print(1)"'}

    Handles escaped parentheses in content: \( and \).
    """
    # Find first unescaped opening parenthesis
    open_idx = _find_first_unescaped_char(rule_string, "(")
    if open_idx == -1:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    # Find last unescaped closing parenthesis
    close_idx = _find_last_unescaped_char(rule_string, ")")
    if close_idx == -1 or close_idx <= open_idx:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    # Ensure closing paren is at the end
    if close_idx != len(rule_string) - 1:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    tool_name = rule_string[:open_idx]
    raw_content = rule_string[open_idx + 1 : close_idx]

    # Missing toolName (e.g., "(foo)") → treat whole string as tool name
    if not tool_name:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(rule_string))

    # Empty content or standalone wildcard → tool-wide rule (no content)
    if raw_content == "" or raw_content == "*":
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(tool_name))

    # Unescape the content
    rule_content = unescape_rule_content(raw_content)
    return PermissionRuleValue(
        tool_name=normalize_legacy_tool_name(tool_name),
        rule_content=rule_content,
    )


def permission_rule_value_to_string(rule: PermissionRuleValue) -> str:
    """Convert a PermissionRuleValue back to its string representation.

    TS permissionRuleValueToString.
    """
    if not rule.rule_content:
        return rule.tool_name
    escaped = escape_rule_content(rule.rule_content)
    return f"{rule.tool_name}({escaped})"


# ---------------------------------------------------------------------------
# Prefix extraction (matching TS shellRuleMatching.ts)
# ---------------------------------------------------------------------------


def extract_prefix(rule: str) -> Optional[str]:
    """Extract prefix from colon-based :* syntax. TS permission_rule_extract_prefix."""
    if rule.endswith(":*"):
        return rule[:-2]
    return None


# ---------------------------------------------------------------------------
# Legacy name normalization
# ---------------------------------------------------------------------------


# Maps legacy tool names to current canonical names (TS LEGACY_TOOL_NAME_ALIASES)
LEGACY_TOOL_NAME_ALIASES: dict[str, str] = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "AgentOutputTool": "TaskOutput",
    "BashOutputTool": "TaskOutput",
}


def normalize_legacy_tool_name(name: str) -> str:
    return LEGACY_TOOL_NAME_ALIASES.get(name, name)


def get_legacy_tool_names(canonical_name: str) -> list[str]:
    result: list[str] = []
    for legacy, canonical in LEGACY_TOOL_NAME_ALIASES.items():
        if canonical == canonical_name:
            result.append(legacy)
    return result
