"""Port of: src/skills/bundledSkills.ts

Bundled skill registry — skills shipped with the CLI.
"""

from __future__ import annotations

from hare.skills.bundled import (
    get_all_bundled_skills,
    get_bundled_skill,
)


def get_bundled_skill_names() -> list[str]:
    """Return the names of all bundled skills."""
    return [s.name for s in get_all_bundled_skills()]


def get_bundled_skill_content(name: str) -> str:
    """Return the content of a bundled skill by name."""
    skill = get_bundled_skill(name)
    return skill.content if skill else ""


def get_bundled_skill_description(name: str) -> str:
    """Return the description of a bundled skill by name."""
    skill = get_bundled_skill(name)
    return skill.description if skill else ""


def get_bundled_skill_count() -> int:
    """Return the total number of bundled skills."""
    return len(get_all_bundled_skills())


def get_bundled_skills_for_prompt() -> str:
    """Format all bundled skill names and descriptions for inclusion in prompts."""
    skills = get_all_bundled_skills()
    if not skills:
        return ""
    lines = ["Available skills:"]
    for s in skills:
        lines.append(f"- {s.name}: {s.description}")
    return "\n".join(lines)


def has_bundled_skill(name: str) -> bool:
    """Check if a bundled skill exists by name."""
    return get_bundled_skill(name) is not None
