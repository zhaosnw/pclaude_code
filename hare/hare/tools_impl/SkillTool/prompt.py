"""Port of: src/tools/SkillTool/prompt.ts"""

from __future__ import annotations

from typing import Any

from hare.constants.xml import COMMAND_NAME_TAG

SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
DEFAULT_CHAR_BUDGET = 8_000
MAX_LISTING_DESC_CHARS = 250


def get_char_budget(context_window_tokens: int | None = None) -> int:
    import os

    env_val = os.environ.get("SLASH_COMMAND_TOOL_CHAR_BUDGET")
    if env_val and env_val.isdigit():
        return int(env_val)
    if context_window_tokens:
        return int(
            context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT
        )
    return DEFAULT_CHAR_BUDGET


def get_command_description(cmd: dict[str, Any]) -> str:
    desc = cmd.get("description", "")
    when = cmd.get("whenToUse", "")
    full = f"{desc} - {when}" if when else desc
    if len(full) > MAX_LISTING_DESC_CHARS:
        return full[: MAX_LISTING_DESC_CHARS - 1] + "\u2026"
    return full


def format_command_description(cmd: dict[str, Any]) -> str:
    name = cmd.get("name", "")
    return f"- {name}: {get_command_description(cmd)}"


def format_commands_within_budget(
    commands: list[dict[str, Any]],
    context_window_tokens: int | None = None,
) -> str:
    if not commands:
        return ""
    budget = get_char_budget(context_window_tokens)
    full_entries = [format_command_description(c) for c in commands]
    full_total = sum(len(e) for e in full_entries) + len(full_entries) - 1
    if full_total <= budget:
        return "\n".join(full_entries)
    min_desc_len = 20
    bundled_indices: set[int] = set()
    for i, cmd in enumerate(commands):
        if cmd.get("type") == "prompt" and cmd.get("source") == "bundled":
            bundled_indices.add(i)
    bundled_chars = sum(
        len(full_entries[i]) + 1 for i in range(len(commands)) if i in bundled_indices
    )
    remaining_budget = budget - bundled_chars
    rest_commands = [c for i, c in enumerate(commands) if i not in bundled_indices]
    if not rest_commands:
        return "\n".join(full_entries)
    rest_name_overhead = (
        sum(len(c.get("name", "")) + 4 for c in rest_commands) + len(rest_commands) - 1
    )
    available = remaining_budget - rest_name_overhead
    max_desc_len = available // len(rest_commands) if rest_commands else 0
    if max_desc_len < min_desc_len:
        return "\n".join(
            full_entries[i]
            if i in bundled_indices
            else f"- {commands[i].get('name', '')}"
            for i in range(len(commands))
        )
    return "\n".join(
        full_entries[i]
        if i in bundled_indices
        else f"- {commands[i].get('name', '')}: {get_command_description(commands[i])[:max_desc_len]}"
        for i in range(len(commands))
    )


def get_prompt(cwd: str = "") -> str:
    return f"""\
Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>" (e.g., "/commit", "/review-pr"), they are referring to a skill. Use this tool to invoke it.

How to invoke:
- Use this tool with the skill name and optional arguments
- Examples:
  - `skill: "pdf"` - invoke the pdf skill
  - `skill: "commit", args: "-m 'Fix bug'"` - invoke with arguments
  - `skill: "review-pr", args: "123"` - invoke with arguments
  - `skill: "ms-office-suite:pdf"` - invoke using fully qualified name

Important:
- Available skills are listed in system-reminder messages in the conversation
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)
- If you see a <{COMMAND_NAME_TAG}> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again
"""
