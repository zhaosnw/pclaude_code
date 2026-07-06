"""Detect skill file changes for hot reload. Port of: skillChangeDetector.ts"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillChangeSnapshot:
    path: Path
    mtime: float


async def snapshot_skill_paths(paths: list[Path]) -> list[SkillChangeSnapshot]:
    out: list[SkillChangeSnapshot] = []
    for p in paths:
        if p.is_file():
            out.append(SkillChangeSnapshot(path=p, mtime=p.stat().st_mtime))
    return out


async def detect_changes(
    before: list[SkillChangeSnapshot],
    after: list[SkillChangeSnapshot],
) -> list[Path]:
    b = {s.path: s.mtime for s in before}
    changed: list[Path] = []
    for s in after:
        old = b.get(s.path)
        if old is None or old != s.mtime:
            changed.append(s.path)
    return changed
