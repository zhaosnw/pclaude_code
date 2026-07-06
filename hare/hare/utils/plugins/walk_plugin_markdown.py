"""Walk plugin trees for markdown/skills. Port of walkPluginMarkdown.ts."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator


def walk_markdown_files(root: str) -> Iterator[Path]:
    base = Path(root)
    if not base.is_dir():
        return
    yield from (p for p in base.rglob("*.md") if p.is_file())
