"""Memory-prompt system-prompt section — port of 2.1.88 loadMemoryPrompt /
buildMemoryLines (src/memdir/memdir.ts + src/memdir/memoryTypes.ts).

Auto-memory is ON by default in 2.1.88 (isAutoMemoryEnabled() final return is
true), so the `memory` dynamic section IS part of the default system prompt.
hare had the memdir data layer but no prompt assembler — this closes that gap.
"""

import pytest

from hare.memdir import memdir as M
from hare.memdir.memory_types import (
    MEMORY_FRONTMATTER_EXAMPLE,
    MEMORY_TYPES,
    TYPES_SECTION_INDIVIDUAL,
)


@pytest.fixture
def tmp_mem(monkeypatch, tmp_path):
    """Redirect the auto-memory dir to a tmp path so prompt-building never
    touches the real ~/.claude. load_memory_prompt() ensures the dir exists."""
    d = str(tmp_path / "memory") + "/"
    monkeypatch.setattr(M, "get_auto_mem_path", lambda: d)
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SIMPLE", raising=False)
    return d


# ---------------------------------------------------------------------------
# load_memory_prompt
# ---------------------------------------------------------------------------

def test_load_memory_prompt_default_returns_section(tmp_mem):
    out = M.load_memory_prompt()
    assert out is not None
    # header + persistent-memory description + dir-exists guidance
    assert "# auto memory" in out
    assert "persistent, file-based memory system at" in out
    assert M.DIR_EXISTS_GUIDANCE in out
    # types taxonomy present
    assert "## Types of memory" in out
    for t in MEMORY_TYPES:
        assert f"<name>{t}</name>" in out
    # what-not-to-save + when-to-access + before-recommending sections
    assert "## What NOT to save in memory" in out
    assert "## When to access memories" in out
    assert "## Before recommending from memory" in out
    assert "## Memory and other forms of persistence" in out


def test_load_memory_prompt_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    assert M.load_memory_prompt() is None


def test_load_memory_prompt_creates_dir(tmp_mem):
    import os

    M.load_memory_prompt()
    assert os.path.isdir(tmp_mem)


# ---------------------------------------------------------------------------
# build_memory_lines — two-step index vs skip-index
# ---------------------------------------------------------------------------

def test_build_memory_lines_two_step_index():
    """Default (skip_index=False): the two-step save with a MEMORY.md pointer."""
    out = "\n".join(M.build_memory_lines("auto memory", "/tmp/mem/"))
    assert "## How to save memories" in out
    assert "two-step process" in out
    assert M.ENTRYPOINT_NAME in out  # MEMORY.md pointer step
    assert f"lines after {M.MAX_ENTRYPOINT_LINES} will be truncated" in out


def test_build_memory_lines_skip_index_omits_pointer():
    out = "\n".join(M.build_memory_lines("auto memory", "/tmp/mem/", skip_index=True))
    assert "## How to save memories" in out
    assert "two-step process" not in out
    assert "add a pointer" not in out


def test_build_memory_lines_extra_guidelines_threaded():
    out = "\n".join(
        M.build_memory_lines("auto memory", "/tmp/mem/", extra_guidelines=["EXTRA-XYZ"])
    )
    assert "EXTRA-XYZ" in out


def test_frontmatter_example_has_type_field():
    fm = "\n".join(MEMORY_FRONTMATTER_EXAMPLE)
    assert "name:" in fm
    assert "description:" in fm
    # type line lists all four types
    assert "type:" in fm
    for t in MEMORY_TYPES:
        assert t in fm


def test_types_section_individual_has_no_scope_tags():
    """INDIVIDUAL mode omits the <scope> qualifier (that's COMBINED/team only)."""
    s = "\n".join(TYPES_SECTION_INDIVIDUAL)
    assert "<scope>" not in s


# ---------------------------------------------------------------------------
# Wiring into the system prompt
# ---------------------------------------------------------------------------

def test_memory_section_in_system_prompt(tmp_mem):
    from hare.constants.prompts import get_system_prompt

    p = get_system_prompt(tools=[], main_loop_model="claude-sonnet-4-20250514")
    assert "# auto memory" in p
    assert "persistent, file-based memory system at" in p


def test_memory_section_after_session_guidance(tmp_mem):
    """2.1.88 dynamicSections order: session_guidance THEN memory."""
    from hare.constants.prompts import get_system_prompt

    p = get_system_prompt(tools=[], main_loop_model="claude-sonnet-4-20250514")
    mem = p.find("# auto memory")
    assert mem != -1
    # session guidance section uses a stable header in hare
    sg = p.find("Session-specific guidance")
    if sg != -1:
        assert sg < mem, "memory should follow session_guidance (2.1.88 order)"


def test_memory_section_absent_when_disabled(monkeypatch):
    from hare.constants.prompts import get_system_prompt

    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    p = get_system_prompt(tools=[], main_loop_model="claude-sonnet-4-20250514")
    assert "# auto memory" not in p
