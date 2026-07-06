"""Tree string rendering (port of treeify.ts)."""

from __future__ import annotations

from typing import Any

DEFAULT_BRANCH = "├"
DEFAULT_LAST = "└"
DEFAULT_LINE = "│"


def treeify(obj: dict[str, Any] | str, *, show_values: bool = True) -> str:
    lines: list[str] = []
    visited: set[int] = set()

    def grow(node: Any, prefix: str, is_last: bool, depth: int) -> None:
        if isinstance(node, str):
            lines.append(prefix + node)
            return
        if not show_values or node is None:
            return
        if isinstance(node, dict):
            oid = id(node)
            if oid in visited:
                lines.append(prefix + "[Circular]")
                return
            visited.add(oid)
            items = list(node.items())
            for i, (k, v) in enumerate(items):
                last = i == len(items) - 1
                branch = DEFAULT_LAST if last else DEFAULT_BRANCH
                if isinstance(v, dict):
                    lines.append(f"{prefix}{branch} {k}:")
                    grow(
                        v,
                        prefix + ("   " if last else f"{DEFAULT_LINE}  "),
                        last,
                        depth + 1,
                    )
                else:
                    lines.append(f"{prefix}{branch} {k}: {v!s}")

    if isinstance(obj, dict):
        grow(obj, "", True, 0)
    else:
        lines.append(str(obj))
    return "\n".join(lines)
