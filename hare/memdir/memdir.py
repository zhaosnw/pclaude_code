"""
MemDir – memory-backed directory for ephemeral file storage.

Port of: src/memdir/memdir.ts
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from hare.memdir.memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    TRUSTING_RECALL_SECTION,
    TYPES_SECTION_INDIVIDUAL,
    WHAT_NOT_TO_SAVE_SECTION,
    WHEN_TO_ACCESS_SECTION,
)
from hare.memdir.paths import (
    get_auto_mem_path,
    is_auto_memory_enabled,
)


@dataclass
class MemDir:
    """In-memory directory for storing session artifacts."""

    base_path: str
    _files: dict[str, str] = field(default_factory=dict)

    def write(self, relative_path: str, content: str) -> None:
        full = os.path.join(self.base_path, relative_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        self._files[relative_path] = content

    def read(self, relative_path: str) -> str | None:
        if relative_path in self._files:
            return self._files[relative_path]
        full = os.path.join(self.base_path, relative_path)
        if os.path.isfile(full):
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
            self._files[relative_path] = content
            return content
        return None

    def exists(self, relative_path: str) -> bool:
        if relative_path in self._files:
            return True
        return os.path.exists(os.path.join(self.base_path, relative_path))

    def list_files(self) -> list[str]:
        result: list[str] = []
        for root, dirs, files in os.walk(self.base_path):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.base_path)
                result.append(rel.replace("\\", "/"))
        return result

    def delete(self, relative_path: str) -> bool:
        self._files.pop(relative_path, None)
        full = os.path.join(self.base_path, relative_path)
        if os.path.isfile(full):
            os.remove(full)
            return True
        return False


# ---------------------------------------------------------------------------
# Memory system constants (TS memdir.ts L34-35, L116-117)
# ---------------------------------------------------------------------------

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
DIR_EXISTS_GUIDANCE = (
    "This directory already exists — write to it directly with the Write tool "
    "(do not run mkdir or check for its existence)."
)


# ---------------------------------------------------------------------------
# Memory-prompt assembler (TS memdir.ts buildMemoryLines / loadMemoryPrompt)
#
# In 2.1.88 auto-memory is ON by default (isAutoMemoryEnabled() → true), so the
# `memory` dynamic section is part of the default system prompt. hare had the
# memdir data layer + the memoryTypes prose constants but no assembler; this is
# the missing piece that wires those into get_system_prompt().
#
# Only the default (auto-memory, individual-directory) path is ported. KAIROS
# daily-log mode and TEAMMEM combined mode are separate feature-gated
# subsystems (off by default) and are intentionally not reproduced here.
# ---------------------------------------------------------------------------


def _feature_value(key: str, default: bool = False) -> bool:
    """Read a GrowthBook feature flag (cached, may be stale), defaulting off.

    Mirrors getFeatureValue_CACHED_MAY_BE_STALE. Defensive: any failure in the
    analytics layer must not break system-prompt assembly."""
    try:
        from hare.services.analytics.growthbook import (
            get_feature_value_cached_may_be_stale,
        )

        return bool(get_feature_value_cached_may_be_stale(key, default))
    except Exception:
        return default


def ensure_memory_dir_exists(memory_dir: str) -> None:
    """Create the memory directory (recursive, idempotent) so the prompt's
    "This directory already exists" promise holds and the model can write
    without first checking. Best-effort — never raises into prompt assembly.

    TS: ensureMemoryDirExists (recursive mkdir, swallows EEXIST)."""
    try:
        os.makedirs(memory_dir, exist_ok=True)
    except OSError:
        pass


def _build_searching_past_context_section(auto_mem_dir: str) -> list[str]:
    """`## Searching past context` — gated behind tengu_coral_fern (off by
    default → returns []). TS: buildSearchingPastContextSection."""
    if not _feature_value("tengu_coral_fern", False):
        return []

    from hare.tools_impl.GrepTool.grep_tool import TOOL_NAME as _GREP  # local

    try:
        from hare.utils.cwd import get_cwd

        project_dir = get_cwd()
    except Exception:
        project_dir = "."
    mem_search = (
        f'{_GREP} with pattern="<search term>" path="{auto_mem_dir}" glob="*.md"'
    )
    transcript_search = (
        f'{_GREP} with pattern="<search term>" path="{project_dir}/" glob="*.jsonl"'
    )
    return [
        "## Searching past context",
        "",
        "When looking for past context:",
        "1. Search topic files in your memory directory:",
        "```",
        mem_search,
        "```",
        "2. Session transcript logs (last resort — large files, slow):",
        "```",
        transcript_search,
        "```",
        "Use narrow search terms (error messages, file paths, function names) "
        "rather than broad keywords.",
        "",
    ]


def build_memory_lines(
    display_name: str,
    memory_dir: str,
    extra_guidelines: list[str] | None = None,
    skip_index: bool = False,
) -> list[str]:
    """Assemble the memory system-prompt section (individual-directory mode).

    Port of buildMemoryLines (src/memdir/memdir.ts). `skip_index` (driven by
    tengu_moth_copse) switches between the two-step MEMORY.md-index save flow
    and the flat one-file-per-memory flow."""
    if skip_index:
        how_to_save = [
            "## How to save memories",
            "",
            "Write each memory to its own file (e.g., `user_role.md`, "
            "`feedback_testing.md`) using this frontmatter format:",
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
        how_to_save = [
            "## How to save memories",
            "",
            "Saving a memory is a two-step process:",
            "",
            "**Step 1** — write the memory to its own file (e.g., "
            "`user_role.md`, `feedback_testing.md`) using this frontmatter "
            "format:",
            "",
            *MEMORY_FRONTMATTER_EXAMPLE,
            "",
            f"**Step 2** — add a pointer to that file in `{ENTRYPOINT_NAME}`. "
            f"`{ENTRYPOINT_NAME}` is an index, not a memory — each entry should "
            f"be one line, under ~150 characters: `- [Title](file.md) — "
            f"one-line hook`. It has no frontmatter. Never write memory content "
            f"directly into `{ENTRYPOINT_NAME}`.",
            "",
            f"- `{ENTRYPOINT_NAME}` is always loaded into your conversation "
            f"context — lines after {MAX_ENTRYPOINT_LINES} will be truncated, "
            f"so keep the index concise",
            "- Keep the name, description, and type fields in memory files "
            "up-to-date with the content",
            "- Organize memory semantically by topic, not chronologically",
            "- Update or remove memories that turn out to be wrong or outdated",
            "- Do not write duplicate memories. First check if there is an "
            "existing memory you can update before writing a new one.",
        ]

    lines: list[str] = [
        f"# {display_name}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`. "
        f"{DIR_EXISTS_GUIDANCE}",
        "",
        "You should build up this memory system over time so that future "
        "conversations can have a complete picture of who the user is, how "
        "they'd like to collaborate with you, what behaviors to avoid or "
        "repeat, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it "
        "immediately as whichever type fits best. If they ask you to forget "
        "something, find and remove the relevant entry.",
        "",
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        "",
        *how_to_save,
        "",
        *WHEN_TO_ACCESS_SECTION,
        "",
        *TRUSTING_RECALL_SECTION,
        "",
        "## Memory and other forms of persistence",
        "Memory is one of several persistence mechanisms available to you as "
        "you assist the user in a given conversation. The distinction is often "
        "that memory can be recalled in future conversations and should not be "
        "used for persisting information that is only useful within the scope "
        "of the current conversation.",
        "- When to use or update a plan instead of memory: If you are about to "
        "start a non-trivial implementation task and would like to reach "
        "alignment with the user on your approach you should use a Plan rather "
        "than saving this information to memory. Similarly, if you already have "
        "a plan within the conversation and you have changed your approach "
        "persist that change by updating the plan rather than saving a memory.",
        "- When to use or update tasks instead of memory: When you need to "
        "break your work in current conversation into discrete steps or keep "
        "track of your progress use tasks instead of saving to memory. Tasks "
        "are great for persisting information about the work that needs to be "
        "done in the current conversation, but memory should be reserved for "
        "information that will be useful in future conversations.",
        "",
        *(extra_guidelines or []),
        "",
    ]

    lines.extend(_build_searching_past_context_section(memory_dir))
    return lines


def load_memory_prompt() -> str | None:
    """Build the `memory` system-prompt section, or None when auto-memory is
    disabled. Port of loadMemoryPrompt (default/auto path only).

    Side effect (matching TS): ensures the auto-memory directory exists so the
    "already exists" guidance is truthful and the model can write directly."""
    if not is_auto_memory_enabled():
        return None

    # Cowork injects memory-policy text via env var; thread it through.
    cowork_extra = os.environ.get("CLAUDE_COWORK_MEMORY_EXTRA_GUIDELINES")
    extra_guidelines = (
        [cowork_extra] if cowork_extra and cowork_extra.strip() else None
    )

    auto_dir = get_auto_mem_path()
    ensure_memory_dir_exists(auto_dir)
    skip_index = _feature_value("tengu_moth_copse", False)
    return "\n".join(
        build_memory_lines("auto memory", auto_dir, extra_guidelines, skip_index)
    )
