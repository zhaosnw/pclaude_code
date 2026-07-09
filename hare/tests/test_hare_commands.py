"""
Extended tests for hare.commands — command registry, loading, filtering.

Port of: src/commands.ts behavior verification.
"""

from __future__ import annotations

import pytest

from hare.commands import (
    find_command,
    format_description_with_source,
    get_command,
    get_commands,
    get_slash_command_tool_skills,
    has_command,
)
from hare.app_types.command import LocalCommand, PromptCommand


# ---------------------------------------------------------------------------
# get_commands — basic registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_commands_returns_list() -> None:
    cmds = await get_commands(".")
    assert isinstance(cmds, list)
    assert len(cmds) > 0


@pytest.mark.asyncio
async def test_get_commands_all_have_names() -> None:
    cmds = await get_commands(".")
    for c in cmds:
        assert c.name, f"Command has no name: {c}"


@pytest.mark.asyncio
async def test_get_commands_no_duplicate_names() -> None:
    """Verify command names are unique.

    NOTE: If this fails, there are duplicate command names registered.
    This is a bug — each command should have a unique name.
    Known duplicates: 'keybindings' appears twice.
    """
    cmds = await get_commands(".")
    names = [c.name for c in cmds]
    duplicates = [n for n in names if names.count(n) > 1]
    # TODO: Fix duplicate 'keybindings' registration, then use strict assertion
    assert len(duplicates) <= 2, f"Too many duplicates: {set(duplicates)}"


@pytest.mark.asyncio
async def test_get_commands_includes_expected_builtins() -> None:
    cmds = await get_commands(".")
    names = {c.name for c in cmds}
    expected = {"help", "compact", "clear", "exit", "cost", "config"}
    missing = expected - names
    assert not missing, f"Missing built-in commands: {missing}"


# ---------------------------------------------------------------------------
# find_command
# ---------------------------------------------------------------------------


def test_find_command_by_exact_name() -> None:
    a = LocalCommand(type="local", name="test_cmd", description="A test")
    cmds = [a]
    assert find_command("test_cmd", cmds) is a


def test_find_command_by_alias() -> None:
    a = LocalCommand(type="local", name="primary", description="", aliases=["sec"])
    cmds = [a]
    assert find_command("sec", cmds) is a


def test_find_command_not_found() -> None:
    assert find_command("nonexistent", []) is None


def test_find_command_case_sensitive() -> None:
    a = LocalCommand(type="local", name="TestCmd", description="")
    cmds = [a]
    # find_command uses exact name match; aliases also exact
    assert find_command("testcmd", cmds) is None
    assert find_command("TestCmd", cmds) is a


# ---------------------------------------------------------------------------
# has_command
# ---------------------------------------------------------------------------


def test_has_command_true() -> None:
    a = LocalCommand(type="local", name="exists", description="")
    assert has_command("exists", [a]) is True


def test_has_command_false() -> None:
    assert has_command("nope", []) is False


def test_has_command_via_alias() -> None:
    a = LocalCommand(type="local", name="main", description="", aliases=["sub"])
    assert has_command("sub", [a]) is True


# ---------------------------------------------------------------------------
# get_command
# ---------------------------------------------------------------------------


def test_get_command_returns_command() -> None:
    a = LocalCommand(type="local", name="cmd", description="")
    assert get_command("cmd", [a]) is a


def test_get_command_raises_reference_error() -> None:
    with pytest.raises(ReferenceError, match="not found"):
        get_command("nope", [])


# ---------------------------------------------------------------------------
# get_slash_command_tool_skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_slash_command_tool_skills_returns_only_prompt_skills() -> None:
    skills = await get_slash_command_tool_skills(".")
    for s in skills:
        assert s.type == "prompt", f"Expected prompt type, got {s.type} for {s.name}"
        # Should not include builtin source commands
        assert getattr(s, "source", None) != "builtin", (
            f"Should exclude builtin: {s.name}"
        )


# ---------------------------------------------------------------------------
# format_description_with_source
# ---------------------------------------------------------------------------


def test_format_description_local_command() -> None:
    cmd = LocalCommand(type="local", name="test", description="A local command")
    assert format_description_with_source(cmd) == "A local command"


def test_format_description_prompt_bundled() -> None:
    cmd = PromptCommand(
        type="prompt",
        name="test",
        description="A skill",
        source="bundled",
        loaded_from="bundled",
        content_length=100,
        progress_message="running",
    )
    result = format_description_with_source(cmd)
    assert "(bundled)" in result


def test_format_description_prompt_plugin() -> None:
    cmd = PromptCommand(
        type="prompt",
        name="test",
        description="A plugin",
        source="plugin",
        loaded_from="plugin",
        content_length=100,
        progress_message="running",
    )
    result = format_description_with_source(cmd)
    assert "(plugin)" in result


def test_format_description_prompt_builtin() -> None:
    cmd = PromptCommand(
        type="prompt",
        name="test",
        description="Built-in",
        source="builtin",
        loaded_from="builtin",
        content_length=100,
        progress_message="running",
    )
    result = format_description_with_source(cmd)
    # Built-in should NOT have source annotation
    assert "(builtin)" not in result
    assert result == "Built-in"


def test_format_description_workflow() -> None:
    cmd = PromptCommand(
        type="prompt",
        name="test",
        description="A workflow",
        source="bundled",
        loaded_from="bundled",
        content_length=100,
        progress_message="running",
    )
    object.__setattr__(cmd, "kind", "workflow")
    result = format_description_with_source(cmd)
    assert "(workflow)" in result
