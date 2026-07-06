"""Combined memory prompt builder (port of src/memdir/teamMemPrompts.ts).

Builds prompt sections that instruct the Claude model about the memory system:
private vs team memory, how to read/write memories, scope decisions,
entrypoint management, and safety considerations.

Usage:
    prompt = build_combined_memory_prompt(extra_guidelines=[...])
    team_prompt = build_team_memory_section()

When team memory is enabled (TENGU_HERRING_CLOCK env),
team-specific sections are included automatically.
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

from hare.memdir.memdir import DIR_EXISTS_GUIDANCE, ENTRYPOINT_NAME as _ENTRYPOINT_NAME, MAX_ENTRYPOINT_LINES as _MAX_ENTRYPOINT_LINES
from hare.memdir.memory_types import (
    MEMORY_DRIFT_CAVEAT,
    MEMORY_FRONTMATTER_EXAMPLE,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_COMBINED,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
)
from hare.memdir.paths import (
    get_auto_mem_entrypoint,
    get_auto_mem_path,
    get_max_entrypoint_lines,
)
from hare.memdir.team_mem_paths import (
    get_team_mem_entrypoint,
    get_team_mem_path,
    is_team_memory_enabled,
)

_log = logging.getLogger(__name__)

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200

# ---------------------------------------------------------------------------
# Private memory prompt sections
# ---------------------------------------------------------------------------


def build_private_memory_section(auto_dir: str | None = None) -> str:
    """Build the private (auto) memory prompt section.

    Describes the user's personal memory directory: what goes there,
    how to use it, and the MEMORY.md entrypoint convention.

    Args:
        auto_dir: Optional override for the auto-memory directory path.
                  If None, resolved automatically. Falls back to a
                  placeholder if resolution fails.
    """
    try:
        ad = auto_dir or get_auto_mem_path()
    except Exception:
        _log.warning("Failed to resolve auto memory path for private section.")
        ad = "~/.claude/memory/"

    try:
        entrypoint = get_auto_mem_entrypoint()
        max_lines = get_max_entrypoint_lines()
    except Exception:
        entrypoint = "MEMORY.md"
        max_lines = 200

    lines = [
        "### Private Memory",
        "",
        f"Your private memory lives at `{ad}`. Memories stored here are visible "
        "only to you and this user — they will never be shared with other users "
        "or contributors.",
        "",
        "Private memory is the right choice for:",
        "- **User** memories — role, preferences, knowledge level, communication style",
        "- **Feedback** memories — personal corrections, style preferences, "
        "validated approaches",
        "- **Project** memories that contain personal notes, private context, "
        "or information not suitable for team-wide sharing",
        "",
        f"The entrypoint file `{entrypoint}` (max {max_lines} lines) serves as "
        "a table-of-contents index. Each memory file should have a pointer line "
        "in the entrypoint:",
        "",
        "```",
        "## [2026-05-15] User prefers terse responses → user-pref-terse.md",
        "```",
        "",
        "When you save a new private memory:",
        "1. Write the memory file with YAML frontmatter (name, description, type)",
        "2. Add an index line to the entrypoint so it is discoverable",
        "3. If the entrypoint exceeds the line cap, remove the oldest or least "
        "relevant entries first",
    ]
    return "\n".join(lines)


def build_searching_past_context_section(_auto_dir: str | None = None) -> list[str]:
    """Build the 'Searching past context' instruction section.

    Teaches the model how to proactively search memory files when
    relevant context might exist in previously-saved memories.

    Args:
        _auto_dir: The auto-memory directory (reserved for future use —
                   currently unused but preserved for API compatibility).
    """
    # _auto_dir is reserved for future use when different search strategies
    # may apply to private vs team memory directories.
    _ = _auto_dir
    return [
        "## Searching past context",
        "",
        "Before answering a query that may benefit from remembered context, "
        "search your memory directories for relevant files:",
        "",
        "- **Keyword match**: scan MEMORY.md entrypoints for topic keywords, "
        "person names, project names, or dates mentioned by the user.",
        "- **Type filter**: if the user asks about their preferences, focus on "
        "`type: user` and `type: feedback` memories. If they ask about project "
        "status or past decisions, focus on `type: project` and `type: reference`.",
        "- **Recency bias**: prefer memories written in the last 30 days. Older "
        "memories may be stale — verify before acting on them.",
        "",
        "When you find a relevant memory, read the full file (not just the "
        "frontmatter). The frontmatter provides a summary, but the body "
        "contains the reasoning and context you need to apply it correctly.",
        "",
        "Do NOT search memory when:",
        "- The user explicitly says to ignore or skip memory",
        "- The question is purely about current code state (use grep/read instead)",
        "- The question is about something you just did in this conversation "
        "(the context is already in the transcript)",
        "",
        "If you search and find nothing, do not mention it — just proceed. "
        "Searching is transparent to the user.",
    ]


# ---------------------------------------------------------------------------
# Team memory prompt sections
# ---------------------------------------------------------------------------


def build_team_memory_section(team_dir: str | None = None) -> str:
    """Build the team memory prompt section.

    Describes the shared team memory directory: what it is, how
    it differs from private memory, and contribution guidelines.

    Args:
        team_dir: Optional override for the team memory directory path.
                  If None, resolved automatically. Falls back to a
                  placeholder if resolution fails.

    Returns:
        Formatted prompt section. If team memory is not enabled and no
        override is provided, returns a brief notice instead of the full
        section.
    """
    # If no override and team memory is not enabled, return a short notice
    if team_dir is None and not is_team_memory_enabled():
        return (
            "### Team Memory\n\n"
            "Team memory is not enabled in this environment. "
            "All memories are private-scoped."
        )

    try:
        td = team_dir or get_team_mem_path()
    except Exception:
        _log.warning("Failed to resolve team memory path for team section.")
        td = "~/.claude/memory/team/"

    try:
        entrypoint = get_team_mem_entrypoint()
        max_lines = get_max_entrypoint_lines()
    except Exception:
        entrypoint = "team/MEMORY.md"
        max_lines = 200

    lines = [
        "### Team Memory",
        "",
        f"Team memory lives at `{td}`. Memories stored here are shared with "
        "**all users and contributors** who work in this project. This is a "
        "collaborative space — what you write today may help another developer "
        "tomorrow, and vice versa.",
        "",
        "Team memory is the right choice for:",
        "- **Project** memories — decisions, constraints, timelines, incident "
        "postmortems, and initiative tracking that benefit the whole team",
        "- **Reference** memories — pointers to external resources (dashboards, "
        "ticket trackers, docs, Slack channels) that everyone needs",
        "- **Feedback** memories — project-wide conventions, testing policies, "
        "build invariants, review standards (NOT personal style preferences)",
        "",
        f"The team entrypoint `{entrypoint}` (max {max_lines} lines) is the "
        "shared index. Every team member can add, read, and update entries here.",
        "",
        "### Team Memory Guidelines",
        "",
        "1. **Write for others.** A team memory is read by people who were not "
        "in the conversation that produced it. Include enough context that "
        "someone can understand the decision without having been there.",
        "",
        "2. **Use absolute dates.** Never write 'next week' or 'last Thursday'. "
        "Always use ISO dates (2026-05-30) so the memory ages correctly.",
        "",
        "3. **Be concise but complete.** A good team memory answers: What was "
        "decided? Why? What are the implications? A bad one just says "
        "'decided to use X' with no reasoning.",
        "",
        "4. **Update, don't duplicate.** If you find an existing memory on the "
        "same topic, update it rather than creating a second one. If the update "
        "changes the conclusion, note when and why it changed.",
        "",
        "5. **Respect existing structure.** Follow the same file naming and "
        "directory conventions already in use by the team.",
        "",
        "6. **No secrets.** Never save API keys, tokens, passwords, or internal "
        "URLs in team memory. The secret scanner will flag and quarantine them.",
    ]
    return "\n".join(lines)


def build_scope_decision_guide() -> list[str]:
    """Build the private-vs-team scope decision guide.

    Returns a prompt section that helps the model decide whether
    a new memory should be private or team-scoped.
    """
    return [
        "## Private vs team: how to choose",
        "",
        "Every memory has a scope: `private` or `team`. Use this decision tree:",
        "",
        "| Question | Private | Team |",
        "|----------|---------|------|",
        "| Is it about the user *personally* (role, preferences, communication)? | Yes | No |",
        "| Is it a personal style preference or correction? | Yes | No |",
        "| Would someone else on the project benefit from knowing this? | No | Yes |",
        "| Is it a project-wide rule, policy, or convention? | No | Yes |",
        "| Is it an external resource everyone should know about? | No | Yes |",
        "| Does it contain private notes, opinions, or half-formed thoughts? | Yes | No |",
        "",
        "When in doubt, bias toward **team** for project and reference memories, "
        "and **private** for user and personal-feedback memories.",
        "",
        "If a team memory would be useful but contains private details, "
        "create two memories: a sanitized team version with the general "
        "insight, and a private version with the personal context.",
    ]


def build_team_memory_contribution_guide() -> list[str]:
    """Build guidelines for contributing to team memory.

    Covers when to write, how to structure, and collaboration etiquette
    when multiple contributors share the same memory directory.
    """
    return [
        "## Contributing to team memory",
        "",
        "### When to write",
        "- When you learn a project-wide decision, constraint, or convention",
        "- When the user shares context that would help future contributors",
        "- When a non-obvious approach was validated by experience",
        "- When the user explicitly asks you to remember something for the team",
        "- After an incident or debugging session reveals something worth documenting",
        "",
        "### When NOT to write",
        "- For personal preferences or communication style (use private memory)",
        "- For information already in CLAUDE.md, README, or code comments",
        "- For ephemeral status that will be stale within days",
        "- For anything you would not want a teammate to see",
        "",
        "### File naming",
        "- Use lowercase kebab-case: `merge-freeze-may-2026.md`",
        "- Include a date prefix for time-sensitive memories: `2026-05-30-incident-postmortem.md`",
        "- Avoid generic names like `notes.md` or `stuff.md`",
        "",
        "### Entrypoint etiquette",
        "- Add your entry at the **top** of MEMORY.md (most recent first)",
        "- Use the format: `## [YYYY-MM-DD] Short description → filename.md`",
        "- If you remove or replace a memory file, also update the entrypoint",
        "- If the entrypoint exceeds the line cap, remove the oldest entries "
        "that are clearly stale (the memory files remain — they just fall "
        "out of the index)",
    ]


def build_team_memory_safety_section() -> list[str]:
    """Build the team memory safety and security section.

    Covers secret scanning, sensitive data awareness, and the
    implications of writing to shared storage.
    """
    return [
        "## Team memory safety",
        "",
        "Team memory is a **shared resource**. Everything you write to it is "
        "visible to everyone on the project, potentially including contractors, "
        "interns, and future hires.",
        "",
        "### Secrets and sensitive data",
        "- **NEVER** save API keys, access tokens, passwords, or private keys",
        "- **NEVER** save internal hostnames, IP addresses, or URLs not meant "
        "for public consumption",
        "- **NEVER** save PII (email addresses, phone numbers, physical addresses) "
        "that are not already public in the repo",
        "- The team memory system runs a **secret scanner** that quarantines "
        "files containing detected secrets. If your memory is quarantined, "
        "you will be notified.",
        "",
        "### When to use private memory instead",
        "- The insight is tied to your personal workflow or preferences",
        "- The information contains private notes or opinions",
        "- You are unsure whether the content is appropriate for team-wide sharing",
        "",
        "When in doubt, save to **private memory first**. You can always "
        "promote it to team memory later after confirming with the user.",
    ]


def build_team_memory_collaboration_section() -> list[str]:
    """Build the team memory collaboration section.

    Describes how multiple users interact with shared memory:
    reading others' contributions, handling conflicts, and
    maintaining coherence.
    """
    return [
        "## Collaborating through team memory",
        "",
        "Team memory is a multi-user system. Other contributors may have "
        "written memories before you, and others will read what you write.",
        "",
        "### Reading others' memories",
        "- When you encounter a team memory, consider who wrote it and when. "
        "A memory from last year may reflect an outdated decision.",
        "- Respect the reasoning in existing memories. If a team memory says "
        "'we use X because Y', follow that convention unless you have clear "
        "evidence it no longer applies.",
        "- If a memory seems wrong or stale, flag it to the user rather than "
        "silently ignoring it. They may want to update or remove it.",
        "",
        "### Building on existing memories",
        "- If you find a partial or outdated memory on a topic, **improve it** "
        "rather than creating a competing memory.",
        "- When updating a memory, add an **Updated:** line at the bottom with "
        "the date and a brief note about what changed, so the history is traceable.",
        "- Never delete a teammate's memory without explicit user approval. "
        "Even if it seems wrong, it may contain context you are missing.",
        "",
        "### Cross-referencing",
        "- When two memories relate to the same topic, add a cross-reference "
        "line: `See also: other-memory.md`",
        "- This helps future readers discover related context without needing "
        "to search the entire directory.",
    ]


# ---------------------------------------------------------------------------
# Memory file organization section
# ---------------------------------------------------------------------------


def build_memory_file_organization() -> list[str]:
    """Build the memory file organization guidelines.

    Describes how individual memory files should be structured:
    frontmatter format, body conventions, and when to split/merge files.
    """
    return [
        "## Memory file organization",
        "",
        "Each memory file follows a consistent structure:",
        "",
        "### Frontmatter (YAML)",
        *MEMORY_FRONTMATTER_EXAMPLE,
        "",
        "### Body structure by type",
        "",
        "**User memories**: Describe the user's role, goals, responsibilities, "
        "knowledge domains, and communication preferences. Use the user's own "
        "words where possible.",
        "",
        "**Feedback memories**:",
        "- Lead with the rule itself: 'When doing X, always Y' or 'Never do Z'",
        "- **Why:** the reason the user gave (incident, preference, policy)",
        "- **How to apply:** when and where this guidance triggers",
        "- **Context:** (optional) the situation where this feedback was given",
        "",
        "**Project memories**:",
        "- Lead with the fact or decision: 'The team decided to X'",
        "- **Why:** the motivation (constraint, deadline, stakeholder ask)",
        "- **How to apply:** how this should shape future suggestions",
        "- **Status:** current state (active, completed, superseded)",
        "",
        "**Reference memories**:",
        "- Lead with the resource: 'Bugs are tracked in X project'",
        "- **URL/path:** the location",
        "- **Purpose:** what to use it for",
        "- **Access:** any notes about who can access it",
        "",
        "### When to split vs merge",
        "- One topic per file. If a memory covers two unrelated things, split it.",
        "- If you find yourself updating the same file repeatedly with related "
        "information, keep them together — the file is a living document.",
    ]


# ---------------------------------------------------------------------------
# Memory scope section (TS combined prompt "## Memory scope")
# ---------------------------------------------------------------------------


def build_memory_scope_section(
    auto_dir: str | None = None,
    team_dir: str | None = None,
) -> list[str]:
    """Build the 'Memory scope' section explaining private vs team scope levels.

    Mirrors the '## Memory scope' section from the TS buildCombinedMemoryPrompt.
    When *team_dir* is None, only the private scope is described.

    Args:
        auto_dir: Path to private memory directory. Resolved if None.
        team_dir: Path to team memory directory. If None, team scope is omitted.

    Returns:
        Prompt lines describing the two scope levels.
    """
    ad = auto_dir or get_auto_mem_path()
    lines: list[str] = [
        "## Memory scope",
        "",
        "There are two scope levels:",
        "",
        f"- **private**: memories that are private between you and the current "
        f"user. They persist across conversations with only this specific user "
        f"and are stored at the root `{ad}`.",
    ]

    if team_dir or is_team_memory_enabled():
        td = team_dir or get_team_mem_path()
        lines.append(
            f"- **team**: memories that are shared with and contributed by all "
            f"of the users who work within this project directory. Team memories "
            f"are synced at the beginning of every session and they are stored "
            f"at `{td}`."
        )
    else:
        lines.append(
            "- **team**: not available in this environment. All memories are "
            "private-scoped."
        )

    return lines


# ---------------------------------------------------------------------------
# Memory intro / explicit commands section
# ---------------------------------------------------------------------------


def build_memory_intro_section() -> list[str]:
    """Build the introductory paragraphs about building up memory over time.

    Mirrors the opening paragraphs from the TS buildCombinedMemoryPrompt
    after the directory description.
    """
    return [
        "You should build up this memory system over time so that future "
        "conversations can have a complete picture of who the user is, how "
        "they'd like to collaborate with you, what behaviors to avoid or "
        "repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it "
        "immediately as whichever type fits best. If they ask you to forget "
        "something, find and remove the relevant entry.",
        "",
    ]


def build_memory_explicit_commands_section() -> list[str]:
    """Build instructions for handling explicit memory commands (remember / forget).

    Standalone version of the explicit-commands paragraph for prompts where
    the intro section is not included.
    """
    return [
        "## Explicit memory commands",
        "",
        "- **Remember**: if the user asks you to remember something, save it "
        "immediately as whichever type fits best. Do not ask for confirmation "
        "unless the scope is ambiguous.",
        "- **Forget**: if the user asks you to forget something, find the "
        "relevant memory file and delete it, then remove its pointer from "
        "the entrypoint. If you cannot find the exact memory, tell the user "
        "what you searched and ask for clarification.",
        "- **Show memories**: if the user asks to see what you remember, read "
        "the entrypoint and list the memories with their type and a brief "
        "description. Do not dump full file contents unless asked.",
        "",
    ]


# ---------------------------------------------------------------------------
# How-to-save section (consolidated, matching TS richness)
# ---------------------------------------------------------------------------


def build_how_to_save_section(
    *,
    skip_index: bool = False,
    include_team: bool = False,
) -> list[str]:
    """Build the 'How to save memories' section with rich guidance.

    Matches the TS buildCombinedMemoryPrompt howToSave, including the
    maintenance bullet points (keep frontmatter updated, organize semantically,
    update/remove stale, don't duplicate).

    Args:
        skip_index: If True, omit entrypoint index instructions and provide
                    a simplified "write directly" flow.
        include_team: If True, mention both private and team directory choices.
    """
    scope_guidance = (
        " in the chosen directory (private or team, per the type's scope guidance)"
        if include_team
        else ""
    )

    entrypoint_name = _ENTRYPOINT_NAME
    max_lines = get_max_entrypoint_lines()

    if skip_index:
        return [
            "## How to save memories",
            "",
            f"Write each memory to its own file{scope_guidance} using this "
            "frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            "- Keep the name, description, and type fields in memory files "
            "up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an "
            "existing memory you can update before writing a new one.",
        ]
    else:
        return [
            "## How to save memories",
            "",
            "Saving a memory is a two-step process:",
            "",
            f"**Step 1** — write the memory to its own file{scope_guidance} "
            "using this frontmatter format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            f"**Step 2** — add a pointer to that file in the same directory's "
            f"`{entrypoint_name}`. Each directory (private and team) has its "
            f"own `{entrypoint_name}` index — each entry should be one line, "
            f"under ~150 characters: `- [Title](file.md) — one-line hook`. "
            f"They have no frontmatter. Never write memory content directly "
            f"into a `{entrypoint_name}`.",
            "",
            f"- Both `{entrypoint_name}` indexes are loaded into your context "
            f"— lines after {max_lines} will be truncated, so keep them concise",
            "- Keep the name, description, and type fields in memory files "
            "up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an "
            "existing memory you can update before writing a new one.",
        ]


# ---------------------------------------------------------------------------
# Memory maintenance guide (standalone)
# ---------------------------------------------------------------------------


def build_memory_maintenance_guide() -> list[str]:
    """Build standalone memory maintenance guidelines.

    Covers when to update, remove, or avoid duplicating memories.
    Can be included independently of the how-to-save section.
    """
    return [
        "## Maintaining memory quality",
        "",
        "- **Keep frontmatter accurate.** The `name`, `description`, and `type` "
        "fields should stay in sync with the file body. If the content changes "
        "significantly, update the frontmatter.",
        "- **Organize semantically.** Group related information by topic, not "
        "by the date you learned it. A memory about testing conventions should "
        "live near other testing memories.",
        "- **Update, don't duplicate.** Before writing a new memory, check for "
        "an existing one on the same topic and improve it instead.",
        "- **Remove stale memories.** If a memory is contradicted by current "
        "code, an updated policy, or the user's own words, delete it rather "
        "than leaving it to mislead future sessions.",
        "- **When updating, note the change.** Add an `Updated:` footer with "
        "the date and a brief reason so the revision history is traceable.",
        "",
    ]


# ---------------------------------------------------------------------------
# Memory vs other persistence (plans, tasks)
# ---------------------------------------------------------------------------


def build_memory_persistence_section() -> list[str]:
    """Build the 'Memory and other forms of persistence' section.

    Mirrors the TS buildCombinedMemoryPrompt section that distinguishes
    memory from plans and tasks.
    """
    return [
        "## Memory and other forms of persistence",
        "",
        "Memory is one of several persistence mechanisms available to you as "
        "you assist the user in a given conversation. The distinction is often "
        "that memory can be recalled in future conversations and should not be "
        "used for persisting information that is only useful within the scope "
        "of the current conversation.",
        "",
        "- **When to use or update a plan instead of memory**: If you are about "
        "to start a non-trivial implementation task and would like to reach "
        "alignment with the user on your approach, you should use a plan rather "
        "than saving this information to memory. Similarly, if you already have "
        "a plan within the conversation and you have changed your approach, "
        "persist that change by updating the plan rather than saving a memory.",
        "- **When to use or update tasks instead of memory**: When you need to "
        "break your work in the current conversation into discrete steps or "
        "keep track of your progress, use tasks instead of saving to memory. "
        "Tasks are great for persisting information about the work that needs "
        "to be done in the current conversation, but memory should be reserved "
        "for information that will be useful in future conversations.",
        "",
    ]


# ---------------------------------------------------------------------------
# Team memory disabled notice
# ---------------------------------------------------------------------------


def build_team_memory_disabled_notice() -> list[str]:
    """Build a notice that team memory is not available.

    Used in prompts when the environment lacks team memory support,
    to prevent the model from attempting to write to a team directory
    that does not exist.
    """
    return [
        "**Team memory is not enabled in this environment.** All memories "
        "should be saved to private memory only. If the user asks you to "
        "share or write team-scoped information, save it as private and "
        "note that team memory is unavailable.",
        "",
    ]


# ---------------------------------------------------------------------------
# Entrypoint format guide
# ---------------------------------------------------------------------------


def build_entrypoint_format_guide() -> list[str]:
    """Build entrypoint (MEMORY.md) formatting guidelines.

    Describes the expected format for entry lines and the line cap.
    """
    entrypoint_name = _ENTRYPOINT_NAME
    max_lines = get_max_entrypoint_lines()

    return [
        "## Entrypoint format",
        "",
        f"Each memory directory has its own `{entrypoint_name}` index file. "
        "Entrypoint entries follow this format:",
        "",
        f"```\n- [Title](file.md) — one-line hook\n```",
        "",
        "- Each entry is a single line, ideally under ~150 characters.",
        f"- The `{entrypoint_name}` file has **no frontmatter**. It is a "
        "plain list of pointers.",
        f"- Never write memory content directly into `{entrypoint_name}`.",
        f"- Lines beyond {max_lines} will be truncated from context. Keep "
        "the entrypoint concise by removing the oldest or least relevant "
        "entries when it approaches the limit.",
        "- Entries are typically ordered most-recent-first, but semantic "
        "grouping is also acceptable.",
        "",
    ]


# ---------------------------------------------------------------------------
# Combined prompt builders
# ---------------------------------------------------------------------------


def build_combined_memory_prompt(
    extra_guidelines: Sequence[str] | None = None,
    skip_index: bool = False,
    include_team: bool | None = None,
) -> str:
    """Build the full combined memory system prompt.

    This is the main entry point for building the memory section of
    the system prompt. It includes private memory instructions and
    (when team memory is enabled) team memory instructions.

    The prompt structure mirrors the TS buildCombinedMemoryPrompt with
    additional sections from the Python-native decomposition.

    Args:
        extra_guidelines: Additional guideline lines to append.
        skip_index: If True, omit the MEMORY.md index management instructions.
        include_team: Override team memory inclusion. If None, auto-detects
                      based on whether team memory is enabled.

    Returns:
        Formatted prompt string ready for inclusion in the system prompt.
    """
    if include_team is None:
        include_team = is_team_memory_enabled()

    # Resolve paths with error handling — if path resolution fails,
    # fall back to a generic placeholder so the prompt is still valid.
    try:
        auto_dir = get_auto_mem_path()
    except Exception:
        _log.warning("Failed to resolve auto memory path; using fallback placeholder.")
        auto_dir = "~/.claude/memory/"

    team_dir: str | None = None
    if include_team:
        try:
            team_dir = get_team_mem_path()
        except Exception:
            _log.warning("Failed to resolve team memory path; using fallback placeholder.")
            team_dir = "~/.claude/memory/team/"

    # Build directory description line with DIR_EXISTS_GUIDANCE
    if include_team and team_dir:
        dir_line = (
            f"You have a persistent, file-based memory system with two "
            f"directories: a private directory at `{auto_dir}` and a shared "
            f"team directory at `{team_dir}`. {DIR_EXISTS_GUIDANCE}"
        )
    else:
        dir_line = (
            f"You have a persistent, file-based **private** memory system at "
            f"`{auto_dir}`. {DIR_EXISTS_GUIDANCE}"
        )

    # Use the consolidated how-to-save builder
    how_to_save = build_how_to_save_section(
        skip_index=skip_index,
        include_team=include_team,
    )

    # Build the sections list — ordering mirrors the TS prompt structure
    sections: list[list[str]] = [
        # === Header ===
        [
            "# Memory",
            "",
        ],
        # === Directory description + DIR_EXISTS_GUIDANCE ===
        [dir_line, ""],
        # === Intro: build up memory over time, remember/forget ===
        build_memory_intro_section(),
        # === Memory scope (private vs team) ===
        build_memory_scope_section(auto_dir=auto_dir, team_dir=team_dir),
        [""],
        # === Types section (combined or individual) ===
        TYPES_SECTION_COMBINED if include_team else TYPES_SECTION_INDIVIDUAL,
        # === What NOT to save ===
        WHAT_NOT_TO_SAVE_SECTION,
        # === Team-safety note (team only) ===
        (
            ["- You MUST avoid saving sensitive data within shared team memories. "
             "For example, never save API keys or user credentials.", ""]
            if include_team
            else []
        ),
        # === Team disabled notice (when team is unavailable) ===
        (build_team_memory_disabled_notice() if not include_team else []),
        # === How to save (with entrypoint or skip_index variant) ===
        how_to_save,
        [""],
        # === When to access ===
        WHEN_TO_ACCESS_SECTION,
        # === Scope decision guide (team only) ===
        (build_scope_decision_guide() if include_team else []),
        # === Trusting recall ===
        TRUSTING_RECALL_SECTION,
        [""],
        # === Memory and other forms of persistence ===
        build_memory_persistence_section(),
        # === Extra user-supplied guidelines ===
        (list(extra_guidelines) if extra_guidelines else []),
        [""] if extra_guidelines else [],
        # === Searching past context ===
        build_searching_past_context_section(auto_dir),
    ]

    # === Team-specific deep sections (appended only when team is enabled) ===
    if include_team:
        team_sections: list[list[str]] = [
            [""],
            build_team_memory_contribution_guide(),
            [""],
            build_team_memory_safety_section(),
            [""],
            build_team_memory_collaboration_section(),
        ]
        sections.extend(team_sections)

    # Flatten all section lists into a single list of lines
    all_lines: list[str] = []
    for section in sections:
        all_lines.extend(section)

    prompt = "\n".join(all_lines)

    _log.debug(
        "Built combined memory prompt: team=%s skip_index=%s extra_guidelines=%s lines=%d",
        include_team,
        skip_index,
        extra_guidelines is not None,
        len(all_lines),
    )

    return prompt


def build_team_only_memory_prompt(
    extra_guidelines: Sequence[str] | None = None,
) -> str:
    """Build a team-memory-only prompt for team-aware operations.

    Use this when the context is team-memory-specific (e.g., team
    memory sync, team memory search, or when displaying team memory
    status to the user).

    If team memory is not enabled, returns a notice explaining
    that team memory is unavailable rather than failing.

    Args:
        extra_guidelines: Additional guideline lines to append.

    Returns:
        Formatted team-memory-only prompt string, or a notice if
        team memory is not enabled.
    """
    if not is_team_memory_enabled():
        _log.debug("build_team_only_memory_prompt called but team memory is disabled.")
        return (
            "# Team Memory\n\n"
            "Team memory is not enabled in this environment. "
            "Enable it by setting `TENGU_HERRING_CLOCK=1` and ensuring "
            "auto-memory is active.\n"
        )

    try:
        team_dir = get_team_mem_path()
    except Exception:
        _log.warning("Failed to resolve team memory path in team-only prompt.")
        team_dir = "~/.claude/memory/team/"

    # Build the team memory section and skip its "### Team Memory" header
    # since we provide our own "# Team Memory" heading.
    team_section_body = build_team_memory_section(team_dir).split("\n")
    # Find the first non-empty, non-header line to start from
    start_idx = 0
    for i, line in enumerate(team_section_body):
        if line.strip() == "### Team Memory":
            start_idx = i + 1
            break
    # Skip leading blank lines after the header
    while start_idx < len(team_section_body) and team_section_body[start_idx].strip() == "":
        start_idx += 1

    lines: list[str] = [
        "# Team Memory",
        "",
        f"Team memory is stored at `{team_dir}`. These memories are shared "
        "with all contributors to this project.",
        "",
        *team_section_body[start_idx:],
        "",
        *build_scope_decision_guide(),
        "",
        *build_team_memory_contribution_guide(),
        "",
        *build_team_memory_safety_section(),
        "",
        *build_team_memory_collaboration_section(),
        "",
        *TRUSTING_RECALL_SECTION,
        *(list(extra_guidelines) if extra_guidelines else []),
    ]

    prompt = "\n".join(lines)
    _log.debug("Built team-only memory prompt: lines=%d", len(lines))
    return prompt


def build_memory_capabilities_summary(
    *,
    private_memory_count: int | None = None,
    team_memory_count: int | None = None,
) -> str:
    """Build a brief, user-facing summary of memory system capabilities.

    Suitable for display in ``/memory`` command output or help text.

    Args:
        private_memory_count: Optional count of private memory files.
        team_memory_count: Optional count of team memory files.

    Returns:
        Formatted summary string.
    """
    try:
        auto_dir = get_auto_mem_path()
    except Exception:
        auto_dir = "~/.claude/memory/"

    try:
        auto_entrypoint = get_auto_mem_entrypoint()
        max_lines = get_max_entrypoint_lines()
    except Exception:
        auto_entrypoint = auto_dir.rstrip("/") + "/MEMORY.md"
        max_lines = 200

    has_team = is_team_memory_enabled()
    team_dir: str | None = None
    team_entrypoint: str | None = None

    if has_team:
        try:
            team_dir = get_team_mem_path()
        except Exception:
            team_dir = None
        try:
            team_entrypoint = get_team_mem_entrypoint() if team_dir else None
        except Exception:
            team_entrypoint = None

    # Header
    summary = "## Memory System\n\n"

    # Private memory section
    summary += "**Private memory** is stored at:\n"
    summary += f"  `{auto_dir}`\n"
    summary += f"  Entrypoint: `{auto_entrypoint}` (max {max_lines} lines)\n"
    summary += "  Types: user, feedback, project, reference\n"
    if private_memory_count is not None:
        label = "file" if private_memory_count == 1 else "files"
        summary += f"  Contains {private_memory_count} {label}\n"
    summary += "\n"

    # Team memory section
    if has_team and team_dir and team_entrypoint:
        summary += "**Team memory** is stored at:\n"
        summary += f"  `{team_dir}`\n"
        summary += f"  Entrypoint: `{team_entrypoint}` (max {max_lines} lines)\n"
        summary += "  Types: project, reference, feedback (team-scoped only)\n"
        if team_memory_count is not None:
            label = "file" if team_memory_count == 1 else "files"
            summary += f"  Contains {team_memory_count} {label}\n"
        summary += "\n"
    else:
        summary += (
            "**Team memory** is not enabled. Set `TENGU_HERRING_CLOCK=1` "
            "to enable shared team memories.\n\n"
        )

    # Formatting guidelines
    summary += (
        "Memory files use YAML frontmatter with `name`, `description`, and "
        "`type` fields. The entrypoint serves as a table-of-contents index "
        "with date-stamped pointers to individual memory files. Use the "
        "format `- [Title](file.md) — one-line hook` for entrypoint entries."
    )

    # Staleness caveat
    summary += (
        "\n\nMemories are point-in-time records. Before acting on a memory, "
        "verify it against the current state of code and project — memories "
        "older than 30 days are more likely to be stale."
    )

    return summary


# ---------------------------------------------------------------------------
# Resolve the appropriate prompt based on context
# ---------------------------------------------------------------------------


def resolve_memory_prompt(
    *,
    extra_guidelines: Sequence[str] | None = None,
    skip_index: bool = False,
    force_team: bool = False,
    force_private: bool = False,
) -> str:
    """Resolve and return the appropriate memory prompt for the current context.

    This is the recommended entry point for most callers. It automatically
    selects between private-only, combined, or team-only prompts based on
    the current environment.

    Args:
        extra_guidelines: Additional guideline lines to append.
        skip_index: If True, omit entrypoint index management instructions.
        force_team: If True, use team-only prompt (ignores private memory).
        force_private: If True, use private-only prompt (ignores team memory).

    Returns:
        The resolved prompt string.

    Raises:
        ValueError: If both force_team and force_private are True.
    """
    if force_team and force_private:
        raise ValueError("Cannot force both team and private memory prompts")

    if force_team:
        _log.debug("Resolving memory prompt: force_team=True")
        return build_team_only_memory_prompt(extra_guidelines=extra_guidelines)

    if force_private:
        _log.debug("Resolving memory prompt: force_private=True")
        return build_combined_memory_prompt(
            extra_guidelines=extra_guidelines,
            skip_index=skip_index,
            include_team=False,
        )

    team_enabled = is_team_memory_enabled()
    _log.debug(
        "Resolving memory prompt: team_enabled=%s skip_index=%s extra_guidelines=%s",
        team_enabled,
        skip_index,
        extra_guidelines is not None,
    )
    return build_combined_memory_prompt(
        extra_guidelines=extra_guidelines,
        skip_index=skip_index,
        include_team=team_enabled,
    )
