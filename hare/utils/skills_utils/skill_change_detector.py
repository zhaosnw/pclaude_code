"""
Skill change detector.

Port of: src/utils/skills/skillChangeDetector.ts
"""

from __future__ import annotations

from typing import Any


def detect_skill_changes(
    old_skills: list[dict[str, Any]],
    new_skills: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Detect added/removed/changed skills."""
    old_by_name = {s.get("name", ""): s for s in old_skills}
    new_by_name = {s.get("name", ""): s for s in new_skills}
    return {
        "added": [s for n, s in new_by_name.items() if n not in old_by_name],
        "removed": [s for n, s in old_by_name.items() if n not in new_by_name],
    }
