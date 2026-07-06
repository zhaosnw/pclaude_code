"""
Prompts for memory extraction.

Port of: src/services/extractMemories/prompts.ts

Two prompt variants:
- build_extract_auto_only_prompt:  single-directory (no team memory)
- build_extract_combined_prompt:   private + team directory support

Both share an `opener` that tells the agent its tool budget and strategy,
then layer on type taxonomy sections, what-not-to-save guidance, and
save-procedure steps.
"""

from __future__ import annotations

from hare.memdir.memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    TYPES_SECTION_COMBINED,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
)

# ---------------------------------------------------------------------------
# Shared opener (TS prompts.ts L29-44)
# ---------------------------------------------------------------------------


def _opener(new_message_count: int, existing_memories: str) -> str:
    """Build the shared opener for both auto-only and combined prompts.

    Tells the forked agent:
    - Its role (memory extraction subagent)
    - Available tools list
    - Turn budget / efficiency strategy
    - Content scope limitation (no investigation / verification)
    - Existing memory manifest (when non-empty)
    """
    manifest = ""
    if existing_memories:
        manifest = (
            "\n\n## Existing memory files\n\n"
            f"{existing_memories}\n\n"
            "Check this list before writing — update an existing file rather "
            "than creating a duplicate."
        )

    return "\n".join([
        f"You are now acting as the memory extraction subagent. Analyze the most recent ~{new_message_count} messages above and use them to update your persistent memory systems.",
        "",
        "Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail and similar), and Edit/Write for paths inside the memory directory only. Bash rm is not permitted. All other tools — MCP, Agent, write-capable Bash, etc — will be denied.",
        "",
        "You have a limited turn budget. Edit requires a prior Read of the same file, so the efficient strategy is: turn 1 — issue all Read calls in parallel for every file you might update; turn 2 — issue all Write/Edit calls in parallel. Do not interleave reads and writes across multiple turns.",
        "",
        f"You MUST only use content from the last ~{new_message_count} messages to update your persistent memories. Do not waste any turns attempting to investigate or verify that content further — no grepping source files, no reading code to confirm a pattern exists, no git commands."
        + manifest,
    ])


# ---------------------------------------------------------------------------
# Save procedure variants
# ---------------------------------------------------------------------------


def _auto_only_how_to_save(skip_index: bool = False) -> list[str]:
    """How-to-save section for auto-only (single-directory) mode.

    Two variants:
    - skip_index=True:  single-step (no MEMORY.md index)
    - skip_index=False: two-step (file + MEMORY.md pointer)
    """
    if skip_index:
        return [
            "## How to save memories",
            "",
            "Write each memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]
    else:
        return [
            "## How to save memories",
            "",
            "Saving a memory is a two-step process:",
            "",
            "**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "**Step 2** — add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory content directly into `MEMORY.md`.",
            "",
            "- `MEMORY.md` is always loaded into your system prompt — lines after 200 will be truncated, so keep the index concise",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]


def _combined_how_to_save(skip_index: bool = False) -> list[str]:
    """How-to-save section for combined (private + team) mode.

    Two variants:
    - skip_index=True:  single-step (no MEMORY.md index, directory choice embedded)
    - skip_index=False: two-step (file + per-directory MEMORY.md pointer)
    """
    if skip_index:
        return [
            "## How to save memories",
            "",
            "Write each memory to its own file in the chosen directory (private or team, per the type's scope guidance) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]
    else:
        return [
            "## How to save memories",
            "",
            "Saving a memory is a two-step process:",
            "",
            "**Step 1** — write the memory to its own file in the chosen directory (private or team, per the type's scope guidance) using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "**Step 2** — add a pointer to that file in the same directory's `MEMORY.md`. Each directory (private and team) has its own `MEMORY.md` index — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. They have no frontmatter. Never write memory content directly into a `MEMORY.md`.",
            "",
            "- Both `MEMORY.md` indexes are loaded into your system prompt — lines after 200 will be truncated, so keep them concise",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.",
        ]


# ---------------------------------------------------------------------------
# Public prompt builders (TS buildExtractAutoOnlyPrompt, buildExtractCombinedPrompt)
# ---------------------------------------------------------------------------


def build_extract_auto_only_prompt(
    new_message_count: int,
    existing_memories: str,
    skip_index: bool = False,
) -> str:
    """Build the extraction prompt for auto-only memory (no team memory).

    TS buildExtractAutoOnlyPrompt (L50-94): four-type taxonomy, no scope
    guidance (single directory).  Uses the individual-type sections (no
    per-type scope blocks since there is only one write destination).
    """
    return "\n".join([
        _opener(new_message_count, existing_memories),
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        "",
        *_auto_only_how_to_save(skip_index),
    ])


def build_extract_combined_prompt(
    new_message_count: int,
    existing_memories: str,
    skip_index: bool = False,
) -> str:
    """Build the extraction prompt for combined auto + team memory.

    TS buildExtractCombinedPrompt (L101-154): four-type taxonomy with per-type
    <scope> guidance — directory choice is baked into each type block so the
    agent knows when to write private vs team without a separate routing section.

    When team memory is not enabled at the feature level, this delegates to
    build_extract_auto_only_prompt.

    Additional team-specific guidance:
    - Sensitive data exclusion (no API keys / credentials in team memories)
    """
    if not _is_team_memory_available():
        return build_extract_auto_only_prompt(
            new_message_count, existing_memories, skip_index
        )

    return "\n".join([
        _opener(new_message_count, existing_memories),
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        *TYPES_SECTION_COMBINED,
        *WHAT_NOT_TO_SAVE_SECTION,
        "- You MUST avoid saving sensitive data within shared team memories. For example, never save API keys or user credentials.",
        "",
        *_combined_how_to_save(skip_index),
    ])


# ---------------------------------------------------------------------------
# Feature flag check
# ---------------------------------------------------------------------------


def _is_team_memory_available() -> bool:
    """Check if team memory (TEAMMEM feature) is available.

    TS: feature('TEAMMEM') gate — when disabled, always use auto-only prompts.
    In the Python port this is controlled by the CLAUDE_CODE_TEAM_MEMORY env var.
    """
    import os
    val = os.environ.get("CLAUDE_CODE_TEAM_MEMORY", "").lower()
    return val in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Legacy simple prompt (retained for backward compatibility)
# ---------------------------------------------------------------------------

MEMORY_EXTRACTION_PROMPT = """Analyze the conversation and extract important facts, preferences, and context that should be remembered for future sessions.

Focus on:
- User preferences and working style
- Project-specific conventions and patterns
- Important decisions and their rationale
- Technical constraints and requirements
- Corrections the user has made

Format each memory as a concise, standalone statement."""
