"""Port of: src/commands/break-cache/. Manage and inspect prompt cache entries."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

from hare.constants.product import VERSION

COMMAND_NAME = "break-cache"
DESCRIPTION = "Show or clear prompt caches (skills, plugins, session)."
ALIASES: list[str] = ["bc", "cache"]

# Well-known cache key prefixes used by the harness to partition entries.
_CACHE_KEYS = {
    "skills": "skills:manifest",
    "plugins": "plugins:registry",
    "session": "session:context",
    "system": "system:prompt",
}

_SCRATCH = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")),
    "cache",
)


def _cache_info() -> list[str]:
    """Collect diagnostic information about the local prompt cache."""
    lines: list[str] = []

    # Show the expected cache directory
    lines.append(f"**Cache directory:** `{_SCRATCH}`")
    if os.path.isdir(_SCRATCH):
        try:
            entries = sorted(os.listdir(_SCRATCH))
            lines.append(f"**Entries on disk:** {len(entries)}")
            total_size = 0
            for name in entries:
                path = os.path.join(_SCRATCH, name)
                if os.path.isfile(path):
                    total_size += os.path.getsize(path)
            lines.append(f"**Total size:** {total_size / 1024:.1f} KiB")
        except OSError:
            lines.append("**Entries on disk:** (unable to read)")
    else:
        lines.append("**Entries on disk:** directory does not exist")

    # Show the logical cache partitions the harness maintains
    lines.append("")
    lines.append("### Cache partitions")
    for label, key_prefix in _CACHE_KEYS.items():
        stable = hashlib.sha256(
            f"{key_prefix}:{VERSION}".encode()
        ).hexdigest()[:12]
        lines.append(f"- **{label}**  →  prefix `{key_prefix}`  |  lookup `{stable}`")

    lines.append("")
    lines.append(
        "Use `/clear-caches` to evict all prompt-cache entries (local and server-side). "
        "The next turn will rebuild from source."
    )
    return lines


async def call(args: list[str], context: Any) -> dict[str, Any]:
    """Show prompt cache state and options for clearing stale entries.

    Inspects the local cache directory, lists known cache partitions used by
    the harness, and directs the user to the full cache-clearing command when
    eviction is needed.
    """
    lines: list[str] = [
        "## Prompt Cache",
        "",
    ]
    lines.extend(_cache_info())
    return {"type": "text", "value": "\n".join(lines)}
