"""Commands registry. Port of: src/commands/ (auto-discovered Python modules)."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable, Optional

from hare.commands_impl.invoke import adapt_command_call

__all__ = ["get_all_command_definitions", "find_command"]

# Helpers / duplicates: prefer the richer module when two map to the same COMMAND_NAME.
_SKIP_MODULES = frozenset(
    {
        "__init__",
        "invoke",
        "add_dir_validation",
        "plugin_parse_args",
        "plugin_use_pagination",
        "rename_generate_session_name",
        "review_ultrareview_enabled",
        "review_remote",
        "mcp_add_command",
        "mcp_xaa_idp_command",
        "clear_conversation",
        "clear_caches",
        "init_verifiers",
        "extra_usage_core",
        "context_noninteractive",
        "create_moved_to_plugin_command",
        "session",
        "diff",
        "clear_cmd",
        "mcp_cmd",
        "permissions_cmd",
    }
)


def _stem_to_default_name(stem: str) -> str:
    base = stem.removesuffix("_cmd")
    return base.replace("_", "-")


def _slash_payload(raw_line: str, command_name: str, aliases: list[str]) -> str:
    """Strip leading ``/name`` so implementations receive the same args as TS."""
    line = raw_line.strip()
    if not line.startswith("/"):
        return line
    tokens = line.split(None, 1)
    head = tokens[0][1:].split("/")[-1].lower()
    keys = {command_name.lower(), *[str(a).lower() for a in aliases]}
    if head in keys:
        return tokens[1] if len(tokens) > 1 else ""
    return line


def _wrap_call(
    raw_fn: Callable[..., Any], command_name: str, aliases: list[str]
) -> Callable[..., Any]:
    async def _wrapped(raw_line: str, context: dict[str, Any]) -> dict[str, Any]:
        payload = _slash_payload(raw_line, command_name, aliases)
        result = await adapt_command_call(raw_fn, payload, context)
        if isinstance(result, dict):
            if "text" not in result:
                if "value" in result:
                    result = {**result, "text": str(result["value"])}
                elif "display_text" in result:
                    result = {**result, "text": str(result["display_text"])}
        return result

    return _wrapped


def _iter_command_module_names() -> list[str]:
    import hare.commands_impl as pkg

    names: list[str] = []
    for mi in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
        if not mi.ispkg:
            short = mi.name.rsplit(".", 1)[-1]
            if short not in _SKIP_MODULES and not short.startswith("_"):
                names.append(short)
    return sorted(names)


def get_all_command_definitions() -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for mod_name in _iter_command_module_names():
        try:
            mod = importlib.import_module(f"hare.commands_impl.{mod_name}")
        except (ImportError, AttributeError):
            continue
        raw_call = getattr(mod, "call", None)
        if raw_call is None:
            continue
        name = getattr(mod, "COMMAND_NAME", None) or _stem_to_default_name(mod_name)
        desc = getattr(mod, "DESCRIPTION", "") or ""
        aliases = list(getattr(mod, "ALIASES", []) or [])
        commands.append(
            {
                "name": str(name),
                "description": str(desc),
                "aliases": aliases,
                "call": _wrap_call(raw_call, str(name), aliases),
            }
        )

    # Dedupe by command name (first wins = lexicographic module order).
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in commands:
        key = c["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        for a in c.get("aliases", []):
            seen.add(str(a).lower())
        unique.append(c)
    return unique


def find_command(name: str) -> Optional[dict[str, Any]]:
    name = name.lower().lstrip("/")
    for cmd in get_all_command_definitions():
        if cmd["name"] == name:
            return cmd
        if name in cmd.get("aliases", []):
            return cmd
    return None
