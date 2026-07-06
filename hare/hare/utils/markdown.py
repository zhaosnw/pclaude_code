"""CLI markdown rendering — port of `markdown.ts` (marked + chalk; simplified)."""

from __future__ import annotations

import re
from typing import Any

_marked_done = False
EOL = "\n"
STRIPPED_TAGS_RE = re.compile(
    r"<(commit_analysis|context|function_analysis|pr_analysis)>.*?</\1>\n?", re.DOTALL
)


def configure_marked() -> None:
    global _marked_done
    _marked_done = True


def strip_prompt_xml_tags(content: str) -> str:
    return STRIPPED_TAGS_RE.sub("", content).strip()


def apply_markdown(content: str, _theme: str, _highlight: Any = None) -> str:
    """Render markdown to ANSI; full implementation would use a lexer."""
    configure_marked()
    return strip_prompt_xml_tags(content)


def pad_aligned(
    content: str, display_width: int, target_width: int, align: str | None
) -> str:
    pad = max(0, target_width - display_width)
    if align == "center":
        left = pad // 2
        return " " * left + content + " " * (pad - left)
    if align == "right":
        return " " * pad + content
    return content + " " * pad
