"""
/skills command - list and manage available skills.

Port of: src/commands/skills/skills.tsx + index.ts

Discovers skills from the filesystem (CLAUDE.md skill definitions,
.claude/skills/ directory, and plugin skills).
"""

from __future__ import annotations

from typing import Any

COMMAND_NAME = "skills"
DESCRIPTION = "List and manage available skills"
ALIASES: list[str] = []


async def call(args: str, **context: Any) -> dict[str, Any]:
    """List available skills."""
    get_available_skills = context.get("get_available_skills")
    get_discovered_skill_names = context.get("get_discovered_skill_names")
    commands = context.get("options", {}).get("commands", [])

    skills = []
    if get_available_skills:
        skills = await get_available_skills()

    if not skills and get_discovered_skill_names:
        skill_names = get_discovered_skill_names()
        skills = [{"name": n, "description": ""} for n in (skill_names or [])]

    if not skills:
        # Fallback: check commands for skill-type entries
        skill_commands = [
            c for c in commands if isinstance(c, dict) and c.get("type") == "skill"
        ]
        if skill_commands:
            skills = [
                {"name": c.get("name", ""), "description": c.get("description", "")}
                for c in skill_commands
            ]

    if not skills:
        return {
            "type": "text",
            "value": "No skills found.\n\nSkills are defined in CLAUDE.md files or the `.claude/skills/` directory.",
        }

    lines = ["## Available Skills", ""]
    for skill in skills:
        name = skill.get("name", "unknown")
        desc = skill.get("description", "")
        if desc:
            lines.append(f"- **/{name}** — {desc}")
        else:
            lines.append(f"- **/{name}**")

    lines.append("")
    lines.append(f"**{len(skills)} skill(s) available.**")

    return {"type": "text", "value": "\n".join(lines)}


def get_command_definition() -> dict[str, Any]:
    return {
        "type": "local",
        "name": COMMAND_NAME,
        "description": DESCRIPTION,
        "aliases": ALIASES,
        "call": call,
    }
