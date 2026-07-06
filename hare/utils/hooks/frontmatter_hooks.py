"""Port of: src/utils/hooks/registerFrontmatterHooks.ts"""

from __future__ import annotations
from typing import Any


def register_frontmatter_hooks(frontmatter: dict[str, Any]) -> None:
    hooks = frontmatter.get("hooks", [])
    if not hooks:
        return
    from hare.utils.hooks.hooks_config_manager import register_hooks

    for hook in hooks:
        event = hook.get("event", "")
        if event:
            register_hooks(event, [hook])
