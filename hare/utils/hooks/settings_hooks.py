"""Load command hooks declared in settings files into the hook registry.

Port of the settings-hook half of src/utils/hooks/hooks.ts. The settings
schema is keyed by event, and each entry pairs a tool-name matcher with the
hooks to run::

    {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}
    ]}}

``hooks_settings.get_all_hooks`` reads a different, flat shape and is not
wired to the runtime; this module is what the CLI uses to make settings hooks
actually execute.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from hare.utils.hooks.hook_registry import get_hook_registry

_SETTINGS_SOURCES = (
    ("userSettings", lambda cwd: Path.home() / ".hare" / "settings.json"),
    ("projectSettings", lambda cwd: Path(cwd) / ".hare" / "settings.json"),
    ("localSettings", lambda cwd: Path(cwd) / ".hare" / "settings.local.json"),
)


def matcher_matches_tool(matcher: str | None, tool_name: str) -> bool:
    """Match a settings hook matcher against a tool name.

    An absent or empty matcher (and ``*``) matches every tool. Otherwise the
    matcher is a regex anchored to the whole name, which also covers the plain
    ``"Bash"`` case.
    """
    if not matcher or matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, tool_name) is not None
    except re.error:
        return matcher == tool_name


def load_settings_hooks(
    cwd: str | None = None,
    settings_file: str | None = None,
) -> list[dict[str, Any]]:
    """Return command hooks declared across the settings files, in order.

    ``settings_file`` is the CLI's --settings flag: an extra source on top of
    the chain, so hooks declared there run like any other settings hook.
    """
    base = cwd or os.getcwd()
    loaded: list[dict[str, Any]] = []
    paths: list[tuple[str, Path]] = [
        (source, resolve(base)) for source, resolve in _SETTINGS_SOURCES
    ]
    if settings_file:
        flag_path = Path(settings_file)
        if not flag_path.is_absolute():
            flag_path = Path(base) / flag_path
        paths.append(("flagSettings", flag_path))
    for source, path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        events = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(events, dict):
            continue
        for event, entries in events.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for hook in entry.get("hooks", []):
                    command = isinstance(hook, dict) and hook.get("command")
                    if not isinstance(command, str) or not command:
                        continue
                    loaded.append(
                        {
                            "event": event,
                            "matcher": entry.get("matcher"),
                            "type": hook.get("type", "command"),
                            "command": command,
                            "source": source,
                        }
                    )
    return loaded


def register_settings_hooks(
    cwd: str | None = None,
    settings_file: str | None = None,
) -> int:
    """Register settings-declared command hooks; returns how many were added."""
    registry = get_hook_registry()
    hooks = load_settings_hooks(cwd, settings_file=settings_file)
    for hook in hooks:
        registry.register(
            hook["event"],
            hook["command"],
            None,
            source=hook["source"],
            matcher=hook.get("matcher"),
            hook_type=hook.get("type", "command"),
        )
    return len(hooks)
