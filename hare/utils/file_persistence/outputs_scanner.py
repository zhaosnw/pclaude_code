"""Scan persisted tool outputs on disk.

Port of: src/utils/filePersistence/outputsScanner.ts
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator


def iter_output_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    yield from root.rglob("*.json")
