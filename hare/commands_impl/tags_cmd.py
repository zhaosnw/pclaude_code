"""Port of: src/commands/tags/. List and manage session tags."""

from __future__ import annotations
from typing import Any


COMMAND_NAME = "tag"
DESCRIPTION = "List or manage conversation session tags"
ALIASES: list[str] = ["tags"]


_TAG_STORE_ATTRS: tuple[str | None, ...] = ("session", None)


def _get_tags(context: Any) -> list[str]:
    """Extract current tags from the session context.

    Checks context.session.tags then context.tags.  Returns a fresh list
    (safe to mutate without side effects) so the caller can call _set_tags
    afterwards to persist the changes.
    """
    for attr in _TAG_STORE_ATTRS:
        current = context if attr is None else getattr(context, attr, None)
        if current is None:
            continue
        tags = getattr(current, "tags", None)
        if isinstance(tags, list):
            return list(tags)
    return []


def _set_tags(context: Any, tags: list[str]) -> bool:
    """Persist tags back to the session context. Returns True on success.

    Writes to the first writable location in the same order _get_tags uses.
    If no store exists yet, creates ``context.tags`` as a fallback.
    """
    for attr in _TAG_STORE_ATTRS:
        current = context if attr is None else getattr(context, attr, None)
        if current is None:
            continue
        if hasattr(current, "tags"):
            try:
                current.tags = list(tags)
                return True
            except (AttributeError, TypeError):
                pass

    # Fallback: create tags directly on the context object.
    try:
        context.tags = list(tags)
        return True
    except (AttributeError, TypeError):
        return False


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """List, add, or remove tags on the current conversation session.

    Usage:
      /tags              — list current tags
      /tags <name>       — add a tag
      /tags -r <name>    — remove a tag
      /tags --clear      — remove all tags
    """
    tags = _get_tags(context)
    modified = False

    if not args:
        # List mode
        if not tags:
            return {
                "type": "text",
                "value": "No session tags. Use `/tag <name>` to add one.",
            }
        lines = [f"Session tags ({len(tags)}):"]
        for t in sorted(tags):
            lines.append(f"  • {t}")
        return {"type": "text", "value": "\n".join(lines)}

    # Parse subcommands
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--clear", "-c"):
            tags.clear()
            modified = True
            i += 1
        elif arg in ("--remove", "-r"):
            i += 1
            if i < len(args):
                target = args[i]
                if target in tags:
                    tags.remove(target)
                    modified = True
                i += 1
        elif arg.startswith("--remove="):
            target = arg.split("=", 1)[1]
            if target in tags:
                tags.remove(target)
                modified = True
            i += 1
        else:
            # Treat as a tag name to add
            if arg not in tags:
                tags.append(arg)
                modified = True
            i += 1

    if modified:
        persisted = _set_tags(context, tags)

    # Build output
    if not tags:
        msg = "All tags cleared."
    else:
        msg = f"Session tags ({len(tags)}): " + ", ".join(tags)
    if modified and not persisted:
        msg += "\n[note: tags were updated in-memory but could not be persisted to session]"

    return {"type": "text", "value": msg}
