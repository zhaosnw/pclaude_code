"""
Command registry and loading.

Port of: src/commands.ts

Loads all command sources (skills, plugins, workflows, built-in) and
``hare.commands_impl`` (Python ports of ``src/commands/**``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Awaitable, Callable, Optional

from hare.app_types.command import (
    Command,
    LocalCommand,
    PromptCommand,
    get_command_name,
    is_command_enabled,
)
from hare.plugins.builtin_plugins import get_builtin_plugin_skill_commands
from hare.skills.bundled import get_all_bundled_skills
from hare.skills.load_skills_dir import load_all_skills
from hare.utils.plugins.load_plugin_commands import get_plugin_commands


# ---------------------------------------------------------------------------
# Built-in commands  (matching COMMANDS() in TS) — stubs if not in commands_impl
# ---------------------------------------------------------------------------


def _make_local(name: str, desc: str, aliases: list[str] | None = None) -> LocalCommand:
    return LocalCommand(
        type="local", name=name, description=desc, aliases=aliases or []
    )


def _make_prompt(
    name: str, desc: str, aliases: list[str] | None = None
) -> PromptCommand:
    return PromptCommand(
        type="prompt", name=name, description=desc, aliases=aliases or []
    )


def _builtin_commands() -> list[Command]:
    """Fallback built-ins when no Python impl registers that name."""
    return [
        _make_local("clear", "Clear the conversation"),
        _make_local("compact", "Compact the conversation context"),
        _make_local("config", "Open the configuration"),
        _make_local("cost", "Show session cost"),
        _make_local("diff", "Show recent changes"),
        _make_local("doctor", "Run diagnostics"),
        _make_local("exit", "Exit the REPL"),
        _make_local("help", "Show available commands"),
        _make_local("memory", "Show or edit HARE.md memory files"),
        _make_local("model", "Switch model"),
        _make_local("resume", "Resume a previous conversation"),
        _make_local("status", "Show session status"),
        _make_local("theme", "Change the theme"),
        _make_local("vim", "Toggle vim mode"),
        _make_prompt("review", "Review code changes"),
        _make_prompt("init", "Initialize a new project"),
    ]


@lru_cache(maxsize=1)
def _builtin_command_names() -> set[str]:
    return {
        name for cmd in _builtin_commands() for name in [cmd.name, *(cmd.aliases or [])]
    }


def _make_local_from_impl(
    name: str,
    description: str,
    aliases: list[str],
    impl_call: Callable[..., Awaitable[dict[str, Any]]],
) -> LocalCommand:
    cmd = LocalCommand(
        type="local",
        name=name,
        description=description,
        aliases=list(aliases or []),
    )

    async def _call(args: str, context: dict[str, Any]) -> dict[str, Any]:
        return await impl_call(args, context)

    object.__setattr__(cmd, "call", _call)
    return cmd


def _commands_from_impl() -> list[LocalCommand]:
    from hare.commands_impl import get_all_command_definitions

    out: list[LocalCommand] = []
    for d in get_all_command_definitions():
        out.append(
            _make_local_from_impl(
                str(d["name"]),
                str(d.get("description") or ""),
                list(d.get("aliases") or []),
                d["call"],
            )
        )
    return out


def _command_lookup_keys(cmd: Command) -> set[str]:
    keys = {cmd.name.lower()}
    keys.update(a.lower() for a in (cmd.aliases or []))
    return keys


# ---------------------------------------------------------------------------
# Skill / Plugin loading (stubs – real impl would read from disk)
# ---------------------------------------------------------------------------


async def _get_skills(cwd: str) -> dict[str, list[Command]]:
    """Load skills from directories, bundled registry, and built-in plugins."""
    skill_dir_commands: list[Command] = []
    plugin_skills: list[Command] = []
    bundled_skills: list[Command] = []
    builtin_plugin_skills: list[Command] = []

    try:
        for skill in load_all_skills(cwd):
            cmd = PromptCommand(
                type="prompt",
                name=skill.name,
                description=skill.description or skill.name,
                source=skill.source,
                loaded_from="skills",
                content_length=len(skill.content),
                progress_message=f"running /{skill.name}",
                has_user_specified_description=bool(skill.description),
            )

            async def _get_prompt_for_command(
                args: str,
                context: dict[str, Any],
                *,
                _content: str = skill.content,
            ) -> str:
                return _content if not args else f"{_content}\n\nUser args:\n{args}"

            object.__setattr__(cmd, "get_prompt_for_command", _get_prompt_for_command)
            skill_dir_commands.append(cmd)
    except Exception:
        skill_dir_commands = []

    try:
        for skill in get_all_bundled_skills():
            cmd = PromptCommand(
                type="prompt",
                name=skill.name,
                description=skill.description,
                source="bundled",
                loaded_from="bundled",
                content_length=len(skill.content),
                progress_message=f"running /{skill.name}",
                has_user_specified_description=True,
            )

            async def _get_prompt_for_command(
                args: str,
                context: dict[str, Any],
                *,
                _content: str = skill.content,
            ) -> str:
                return _content if not args else f"{_content}\n\nUser args:\n{args}"

            object.__setattr__(cmd, "get_prompt_for_command", _get_prompt_for_command)
            bundled_skills.append(cmd)
    except Exception:
        bundled_skills = []

    try:
        for raw in get_builtin_plugin_skill_commands():
            cmd = PromptCommand(
                type="prompt",
                name=str(raw.get("name", "")),
                description=str(raw.get("description", "")),
                source="bundled",
                loaded_from="bundled",
                content_length=int(raw.get("contentLength", 0) or 0),
                progress_message=str(raw.get("progressMessage", "running")),
                has_user_specified_description=bool(
                    raw.get("hasUserSpecifiedDescription", False)
                ),
                when_to_use=raw.get("whenToUse"),
            )
            getter = raw.get("getPromptForCommand")
            if callable(getter):
                object.__setattr__(cmd, "get_prompt_for_command", getter)
            builtin_plugin_skills.append(cmd)
    except Exception:
        builtin_plugin_skills = []

    try:
        for raw in get_plugin_commands():
            if raw.get("type") != "prompt":
                continue
            cmd = PromptCommand(
                type="prompt",
                name=str(raw.get("name", "")),
                description=str(raw.get("description", "")),
                source="plugin",
                loaded_from="plugin",
                content_length=int(raw.get("contentLength", 0) or 0),
                progress_message=str(raw.get("progressMessage", "running")),
                has_user_specified_description=bool(
                    raw.get("hasUserSpecifiedDescription", False)
                ),
                when_to_use=raw.get("whenToUse"),
            )
            getter = raw.get("getPromptForCommand")
            if callable(getter):
                object.__setattr__(cmd, "get_prompt_for_command", getter)
            plugin_skills.append(cmd)
    except Exception:
        plugin_skills = []

    return {
        "skill_dir_commands": skill_dir_commands,
        "plugin_skills": plugin_skills,
        "bundled_skills": bundled_skills,
        "builtin_plugin_skills": builtin_plugin_skills,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_commands(cwd: str) -> list[Command]:
    """
    Returns commands available to the current user.

    Merges skill/plugin placeholders with Python ``commands_impl`` definitions,
    then adds stub built-ins only for names not already covered.
    """
    skills = await _get_skills(cwd)
    impl_locals = _commands_from_impl()
    covered: set[str] = set()
    for cmd in impl_locals:
        covered |= _command_lookup_keys(cmd)

    builtins_extra: list[Command] = []
    for b in _builtin_commands():
        if _command_lookup_keys(b) & covered:
            continue
        builtins_extra.append(b)
        covered |= _command_lookup_keys(b)

    all_commands: list[Command] = [
        *skills["bundled_skills"],
        *skills["builtin_plugin_skills"],
        *skills["skill_dir_commands"],
        *skills["plugin_skills"],
        *impl_locals,
        *builtins_extra,
    ]
    return [cmd for cmd in all_commands if is_command_enabled(cmd)]


def find_command(command_name: str, commands: list[Command]) -> Optional[Command]:
    """Find a command by name or alias."""
    for cmd in commands:
        if (
            cmd.name == command_name
            or get_command_name(cmd) == command_name
            or command_name in (cmd.aliases or [])
        ):
            return cmd
    return None


def has_command(command_name: str, commands: list[Command]) -> bool:
    return find_command(command_name, commands) is not None


def get_command(command_name: str, commands: list[Command]) -> Command:
    cmd = find_command(command_name, commands)
    if cmd is None:
        available = ", ".join(sorted(c.name for c in commands))
        raise ReferenceError(
            f"Command {command_name} not found. Available commands: {available}"
        )
    return cmd


async def get_slash_command_tool_skills(cwd: str) -> list[Command]:
    """
    Filter commands to include only skills. Skills are commands that provide
    specialized capabilities for the model to use.
    """
    all_commands = await get_commands(cwd)
    return [
        cmd
        for cmd in all_commands
        if cmd.type == "prompt"
        and cmd.source != "builtin"
        and (
            getattr(cmd, "has_user_specified_description", False)
            or getattr(cmd, "when_to_use", None)
        )
    ]


def format_description_with_source(cmd: Command) -> str:
    """Formats a command's description with its source annotation for user-facing UI."""
    if cmd.type != "prompt":
        return cmd.description
    if getattr(cmd, "kind", None) == "workflow":
        return f"{cmd.description} (workflow)"
    if cmd.source == "plugin":
        return f"{cmd.description} (plugin)"
    if cmd.source in ("builtin", "mcp"):
        return cmd.description
    if cmd.source == "bundled":
        return f"{cmd.description} (bundled)"
    return cmd.description
